"""Tool registry: maps tool names to their JSON schema and async handler.

The agent loop passes SCHEMAS to the model and dispatches tool calls through
HANDLERS. Adding a capability = add a module here and register it below.
"""

from . import browse, memory_tools, web_fetch, web_search

# name -> {"schema": <openai tool schema>, "handler": <async fn(**args) -> str>}
_TOOLS = {
    "web_search": {"schema": web_search.SCHEMA, "handler": web_search.web_search},
    "web_fetch": {"schema": web_fetch.SCHEMA, "handler": web_fetch.web_fetch},
    # Use the serialized wrapper so only one browser runs at a time.
    "browse": {"schema": browse.SCHEMA, "handler": browse.browse_serialized},
    "remember": {"schema": memory_tools.REMEMBER_SCHEMA, "handler": memory_tools.remember},
    "recall_memory": {"schema": memory_tools.RECALL_SCHEMA, "handler": memory_tools.recall_memory},
}

SCHEMAS = [t["schema"] for t in _TOOLS.values()]


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
