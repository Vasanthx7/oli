"""Tool registry: the single place capabilities are declared.

Each entry pairs an OpenAI-style JSON schema with an async handler. `langchain_tools()`
adapts these into LangChain `StructuredTool`s for the LangGraph agent, so adding a
capability is still just one entry here. `run_tool`/`SCHEMAS` remain for direct
dispatch and tests.
"""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langchain_core.tools import StructuredTool

from ..config import settings
from ..logging_config import get_logger
from . import browse, memory_tools, web_fetch, web_search

log = get_logger(__name__)


def _with_timeout(
    handler: Callable[..., Awaitable[str]], name: str, timeout: int
) -> Callable[..., Awaitable[str]]:
    """Wrap a tool handler in a hard timeout, returned to the model as a string.

    functools.wraps keeps the handler's signature/annotations visible so
    StructuredTool still infers the arg schema from the original handler."""

    @functools.wraps(handler)
    async def wrapper(*args, **kwargs) -> str:
        try:
            return await asyncio.wait_for(handler(*args, **kwargs), timeout=timeout)
        except TimeoutError:
            log.warning("tool_timeout", tool=name, timeout=timeout)
            return f"{name} timed out after {timeout}s and was stopped."

    return wrapper


class Tool(TypedDict):
    schema: dict
    handler: Callable[..., Awaitable[str]]


# name -> {"schema": <openai tool schema>, "handler": <async fn(**args) -> str>}
_TOOLS: dict[str, Tool] = {
    "web_search": {"schema": web_search.SCHEMA, "handler": web_search.web_search},
    "web_fetch": {"schema": web_fetch.SCHEMA, "handler": web_fetch.web_fetch},
    # Use the serialized wrapper so only one browser runs at a time.
    "browse": {"schema": browse.SCHEMA, "handler": browse.browse_serialized},
    "remember": {"schema": memory_tools.REMEMBER_SCHEMA, "handler": memory_tools.remember},
    "recall_memory": {"schema": memory_tools.RECALL_SCHEMA, "handler": memory_tools.recall_memory},
}

SCHEMAS: list[dict] = [t["schema"] for t in _TOOLS.values()]


def langchain_tools() -> list[StructuredTool]:
    """Adapt the registry into LangChain StructuredTools for the LangGraph agent.

    Name and description come from each tool's schema; the argument schema is
    inferred from the handler's type hints. The async handler is used directly as
    the tool's coroutine.
    """
    timeout = settings.tool_timeout_seconds
    tools: list[StructuredTool] = []
    for name, tool in _TOOLS.items():
        fn = tool["schema"]["function"]
        handler = tool["handler"]
        # Cap each tool call so a hung tool can't stall a turn (0 = disabled).
        coroutine = _with_timeout(handler, name, timeout) if timeout > 0 else handler
        tools.append(
            StructuredTool.from_function(
                coroutine=coroutine,
                name=name,
                description=fn["description"],
            )
        )
    return tools


def has_tool(name: str) -> bool:
    return name in _TOOLS


async def run_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name. Returns a string result for the model."""
    tool = _TOOLS.get(name)
    if tool is None:
        return f"Unknown tool: {name}"
    try:
        return await tool["handler"](**args)
    except TypeError as e:
        # Wrong/extra arguments from the model — report instead of crashing the loop.
        return f"{name} called with invalid arguments {args}: {e}"
