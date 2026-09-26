"""Fetch a URL and extract its main text content (clean article text)."""

import asyncio

import httpx

from ..net_guard import UnsafeURLError, validate_url

MAX_LEN = 6000
# Follow redirects manually so each hop is re-checked — an allowed public URL must
# not be able to bounce us to an internal/metadata address (169.254.169.254, etc).
MAX_REDIRECTS = 5
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


async def _get_checked(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET ``url``, following redirects manually and re-validating every hop."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        resp = await client.get(current)
        location = resp.headers.get("location")
        if resp.is_redirect and location:
            current = validate_url(str(resp.url.join(location)))
            continue
        return resp
    raise UnsafeURLError("too many redirects")


async def web_fetch(url: str) -> str:
    try:
        target = validate_url(url)
    except UnsafeURLError as e:
        return f"web_fetch refused for {url!r}: {e}"
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=20.0, headers=_HEADERS
        ) as client:
            resp = await _get_checked(client, target)
            resp.raise_for_status()
            html = resp.text
        text = await asyncio.to_thread(_extract, html, target)
        return f"Content of {target}:\n\n{text}"
    except UnsafeURLError as e:
        return f"web_fetch refused for {url!r}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"web_fetch failed for {target}: {e}"


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
