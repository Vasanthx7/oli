"""Loads the assistant's system prompt (personality) from personality.md.

Kept in a plain markdown file at the project root so the persona can be edited
without touching code. Read fresh each call so edits show up on the next turn.
"""

from .config import PERSONALITY_FILE

_FALLBACK = (
    "You are a helpful personal AI assistant with access to web search, "
    "page fetching, and autonomous browsing tools. Be concise and accurate, "
    "and ground factual claims in tool results."
)


def system_prompt() -> str:
    try:
        text = PERSONALITY_FILE.read_text(encoding="utf-8").strip()
        return text or _FALLBACK
    except FileNotFoundError:
        return _FALLBACK
