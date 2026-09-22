import asyncio
import ipaddress

import httpx
import respx

from oli import net_guard, tools


def _stub_public_dns(monkeypatch):
    """Resolve any host to a public IP so the SSRF guard passes offline (no real DNS)."""
    monkeypatch.setattr(net_guard, "_resolve", lambda host: [ipaddress.ip_address("93.184.216.34")])


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
async def test_web_fetch_extracts_text(monkeypatch):
    _stub_public_dns(monkeypatch)
    html = "<html><body><article><p>Hello world content here.</p></article></body></html>"
    respx.get("https://example.test/page").mock(return_value=httpx.Response(200, text=html))
    result = await tools.run_tool("web_fetch", {"url": "https://example.test/page"})
    assert "example.test/page" in result


@respx.mock
async def test_web_fetch_handles_http_error(monkeypatch):
    _stub_public_dns(monkeypatch)
    respx.get("https://bad.test/").mock(return_value=httpx.Response(500))
    result = await tools.run_tool("web_fetch", {"url": "https://bad.test/"})
    assert "web_fetch failed" in result


@respx.mock
async def test_web_fetch_refuses_private_host(monkeypatch):
    # A host that resolves to a private/metadata address is refused before any request.
    monkeypatch.setattr(
        net_guard, "_resolve", lambda host: [ipaddress.ip_address("169.254.169.254")]
    )
    route = respx.get("https://evil.test/").mock(return_value=httpx.Response(200, text="secret"))
    result = await tools.run_tool("web_fetch", {"url": "https://evil.test/"})
    assert "refused" in result
    assert not route.called  # never left the process


async def test_web_fetch_refuses_file_scheme():
    result = await tools.run_tool("web_fetch", {"url": "file:///etc/passwd"})
    assert "refused" in result


@respx.mock
async def test_web_fetch_refuses_redirect_to_private_host(monkeypatch):
    # An allowed public URL must not be able to bounce us to an internal target.
    def resolve(host):
        return [ipaddress.ip_address("93.184.216.34" if host == "ok.test" else "127.0.0.1")]

    monkeypatch.setattr(net_guard, "_resolve", resolve)
    respx.get("https://ok.test/").mock(
        return_value=httpx.Response(302, headers={"location": "http://internal.test/"})
    )
    internal = respx.get("http://internal.test/").mock(
        return_value=httpx.Response(200, text="secret")
    )
    result = await tools.run_tool("web_fetch", {"url": "https://ok.test/"})
    assert "refused" in result
    assert not internal.called
