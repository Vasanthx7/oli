"""Tools that let the model explicitly write to and read from long-term memory.

These call into the active MemoryStore singleton (set at app startup). Automatic
recall still happens every turn in the agent loop; these give the model direct
control when the user says "remember that..." or asks "what do you know about me?".
"""

import asyncio

from .. import memory


async def remember(content: str) -> str:
    store = memory.active()
    if store is None:
        return "Memory is not available."
    result = await asyncio.to_thread(store.remember, content, "explicit")
    if result.get("stored"):
        return f"Saved to memory: {result['content']}"
    if result.get("reason") == "duplicate":
        return f"Already remembered something similar: {result.get('of')}"
    return "Nothing to remember."


async def recall_memory(query: str) -> str:
    store = memory.active()
    if store is None:
        return "Memory is not available."
    hits = await asyncio.to_thread(store.recall, query)
    if not hits:
        return "No relevant memories found."
    return "Relevant memories:\n" + "\n".join(f"- {h['content']}" for h in hits)


REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "Save a durable fact about the user to long-term memory so you recall it in "
            "future conversations. Use when the user shares a lasting preference, personal "
            "detail, goal, or asks you to remember something. Write the fact concisely in "
            "the third person (e.g. 'The user prefers dark mode')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember."}
            },
            "required": ["content"],
        },
    },
}

RECALL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "recall_memory",
        "description": (
            "Search long-term memory for facts about the user relevant to a query. Use when "
            "you need to recall something the user told you earlier that isn't already in "
            "the conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look up in memory."}
            },
            "required": ["query"],
        },
    },
}
