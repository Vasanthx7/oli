"""Long-term memory: what makes Oli remember you across conversations.

A memory is a short durable fact about the user (a preference, a personal detail,
an ongoing project, a relationship). Memories are embedded and stored in SQLite;
recall is brute-force cosine similarity in NumPy — more than fast enough for the
hundreds-to-thousands of memories a personal assistant accumulates.

Three write/read paths:
  - remember():      store a fact (used by the `remember` tool), with dedupe
  - recall():        top-k similar memories for a query (auto-injected each turn,
                     and exposed via the `recall_memory` tool)
  - extract_facts(): after a turn, ask the LLM what's worth remembering, store each
"""

import asyncio
import json

from . import embeddings
from .storage import Storage

# Recall tuning (bge-small cosine scores; relevant hits typically > 0.55).
RECALL_TOP_K = 6
RECALL_THRESHOLD = 0.55
# Two memories closer than this are treated as the same fact (skip the new one).
DEDUPE_THRESHOLD = 0.90

_EXTRACTION_SYSTEM = (
    "You extract durable facts worth remembering long-term about the user from a "
    "conversation snippet. Remember only stable, personal, reusable facts: the user's "
    "preferences, personal details, ongoing projects, goals, relationships, and how "
    "they like to work. Do NOT remember transient task details, one-off questions, "
    "general knowledge, or anything about you (the assistant).\n\n"
    "Respond with ONLY a JSON array of concise self-contained fact strings written in "
    'the third person about the user (e.g. ["The user prefers Python", "The user is '
    'building a personal AI assistant"]). If nothing is worth saving, respond with [].'
)


class MemoryStore:
    def __init__(self, store: Storage):
        self._store = store
        self._embedder = embeddings.Embedder.instance()

    # --- write ----------------------------------------------------------

    def remember(self, content: str, source: str = "explicit") -> dict:
        """Embed and store a fact, unless a near-duplicate already exists."""
        content = content.strip()
        if not content:
            return {"stored": False, "reason": "empty"}

        vec = self._embedder.embed_document(content)

        # Dedupe against existing memories.
        for mem in self._store.get_memories():
            existing = embeddings.from_blob(mem["embedding"])
            if embeddings.cosine(vec, existing) >= DEDUPE_THRESHOLD:
                return {"stored": False, "reason": "duplicate", "of": mem["content"]}

        mid = self._store.add_memory(content, embeddings.to_blob(vec), source)
        return {"stored": True, "id": mid, "content": content}

    # --- read -----------------------------------------------------------

    def recall(
        self, query: str, k: int = RECALL_TOP_K, threshold: float = RECALL_THRESHOLD
    ) -> list[dict]:
        """Return up to k memories most similar to the query, above threshold."""
        query = query.strip()
        if not query:
            return []
        memories = self._store.get_memories()
        if not memories:
            return []

        qvec = self._embedder.embed_query(query)
        scored = []
        for mem in memories:
            vec = embeddings.from_blob(mem["embedding"])
            score = embeddings.cosine(qvec, vec)
            if score >= threshold:
                scored.append({"content": mem["content"], "score": score, "id": mem["id"]})
        scored.sort(key=lambda m: m["score"], reverse=True)
        return scored[:k]

    # --- extraction -----------------------------------------------------

    async def extract_facts(self, llm, user_message: str, assistant_message: str) -> list[str]:
        """Ask the LLM for durable facts in this exchange and store them. Best-effort."""
        snippet = f"User: {user_message}\nAssistant: {assistant_message}"
        try:
            raw = await llm.complete(
                [
                    {"role": "system", "content": _EXTRACTION_SYSTEM},
                    {"role": "user", "content": snippet},
                ]
            )
            facts = _parse_facts(raw)
        except Exception:
            return []

        stored = []
        for fact in facts:
            # Storage/embedding is blocking; keep the event loop free.
            result = await asyncio.to_thread(self.remember, fact, "auto")
            if result.get("stored"):
                stored.append(fact)
        return stored


def _parse_facts(raw: str) -> list[str]:
    """Parse a JSON array of fact strings out of the model's reply, tolerating fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [str(x).strip() for x in data if isinstance(x, str) and x.strip()]


# --- module-level singleton so the memory tools can reach the active store ----

_active: MemoryStore | None = None


def set_active(store: MemoryStore) -> None:
    global _active
    _active = store


def active() -> MemoryStore | None:
    return _active
