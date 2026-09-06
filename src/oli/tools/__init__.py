"""Tool registry: the single place capabilities are declared.

Each entry pairs an OpenAI-style JSON schema with an async handler. `langchain_tools()`
adapts these into LangChain `StructuredTool`s for the LangGraph agent, so adding a
capability is still just one entry here. `run_tool`/`SCHEMAS` remain for direct
dispatch and tests.
"""

from collections.abc import Awaitable, Callable
from typing import TypedDict

from langchain_core.tools import StructuredTool

from . import browse, memory_tools, web_fetch, web_search


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
    tools: list[StructuredTool] = []
    for name, tool in _TOOLS.items():
        fn = tool["schema"]["function"]
        tools.append(
            StructuredTool.from_function(
                coroutine=tool["handler"],
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
