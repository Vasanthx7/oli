import asyncio

import httpx
import respx

from oli import tools


def test_registry_exposes_expected_tools():
    names = {s["function"]["name"] for s in tools.SCHEMAS}
    assert {"web_search", "web_fetch", "browse", "remember", "recall_memory"} <= names


async def test_run_unknown_tool_returns_message():
    result = await tools.run_tool("does_not_exist", {})
    assert "Unknown tool" in result


async def test_tool_timeout_returns_friendly_message():
    async def slow(query: str) -> str:
        await asyncio.sleep(5)
        return "done"

    wrapped = tools._with_timeout(slow, "slow_tool", timeout=0)
    result = await wrapped(query="x")
    assert "slow_tool timed out" in result


async def test_langchain_tools_preserve_arg_schema_when_wrapped():
    # Timeout wrapping must not hide the handler's arg schema (functools.wraps).
    by_name = {t.name: set(t.args) for t in tools.langchain_tools()}
    assert by_name["web_search"] == {"query"}
    assert by_name["browse"] == {"goal", "profile"}


@respx.mock
async def test_web_fetch_extracts_text():
    html = "<html><body><article><p>Hello world content here.</p></article></body></html>"
    respx.get("https://example.test/page").mock(return_value=httpx.Response(200, text=html))
    result = await tools.run_tool("web_fetch", {"url": "https://example.test/page"})
    assert "example.test/page" in result


@respx.mock
async def test_web_fetch_handles_http_error():
    respx.get("https://bad.test/").mock(return_value=httpx.Response(500))
    result = await tools.run_tool("web_fetch", {"url": "https://bad.test/"})
    assert "web_fetch failed" in result
