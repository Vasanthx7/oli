"""Web search tool backed by DuckDuckGo (via the `ddgs` package) — no API key needed."""

import asyncio

MAX_RESULTS = 6


def _search_sync(query: str) -> str:
    # Imported lazily so a missing/renamed dependency doesn't break app import.
    from ddgs import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=MAX_RESULTS))

    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for '{query}':", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("href") or r.get("url", "")
        body = (r.get("body") or "").strip()
        lines.append(f"{i}. {title}\n   {url}\n   {body}")
    return "\n".join(lines)


async def web_search(query: str) -> str:
    """Run the blocking search in a worker thread so we don't block the event loop."""
    try:
        return await asyncio.to_thread(_search_sync, query)
    except Exception as e:  # noqa: BLE001 — tool results are surfaced to the model as text
        return f"web_search failed: {e}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information, news, facts, or to find relevant "
            "pages. Returns a list of titles, URLs, and snippets. Use this first for "
            "quick lookups before fetching or browsing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                }
            },
            "required": ["query"],
        },
    },
}
