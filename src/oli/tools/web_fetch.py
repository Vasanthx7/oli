"""Fetch a URL and extract its main text content (clean article text)."""

import asyncio

import httpx

MAX_LEN = 6000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _extract(html: str, url: str) -> str:
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    if not text:
        return f"(Could not extract readable text from {url}.)"
    text = text.strip()
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN] + "\n... (truncated)"
    return text


async def web_fetch(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=20.0, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        text = await asyncio.to_thread(_extract, html, url)
        return f"Content of {url}:\n\n{text}"
    except Exception as e:  # noqa: BLE001
        return f"web_fetch failed for {url}: {e}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a specific URL and return its main readable text content. Use when "
            "you have a URL (often from web_search) and need to read the full page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch.",
                }
            },
            "required": ["url"],
        },
    },
}
