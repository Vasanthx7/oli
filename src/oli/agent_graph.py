"""The agent as a LangGraph StateGraph.

Replaces the hand-rolled observe->think->act loop (see ADR 0002/0003). The graph is
the canonical ReAct shape:

    START -> agent -> (tool calls?) -> tools -> agent -> ... -> END

The `agent` node injects the personality prompt and any recalled memories at call
time (they live outside the persisted message state), binds the tools, and calls
the model. `tools_condition` routes to the ToolNode whenever the model requests a
tool, then back to the agent. The LLM is provider-agnostic: a ChatOpenAI client
pointed at Groq's OpenAI-compatible endpoint (swap via settings — see ADR 0006).
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import SecretStr

from . import memory, tools
from .config import settings
from .personality import system_prompt

# Safety rail: max agent<->tools laps before LangGraph aborts (was MAX_TOOL_ITERATIONS).
RECURSION_LIMIT = 16


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build_model() -> ChatOpenAI:
    """Provider-agnostic chat model pointed at the configured endpoint."""
    return ChatOpenAI(
        model=settings.groq_model,
        api_key=SecretStr(settings.groq_api_key),
        base_url=settings.groq_base_url,
        temperature=0.7,
    )


def _recall_block(state_messages: list) -> str | None:
    """Build a system block of memories relevant to the latest user message."""
    mem = memory.active()
    if mem is None:
        return None
    last_human = next((m for m in reversed(state_messages) if isinstance(m, HumanMessage)), None)
    if last_human is None:
        return None
    try:
        hits = mem.recall(str(last_human.content))
    except Exception:
        return None
    if not hits:
        return None
    facts = "\n".join(f"- {h['content']}" for h in hits)
    return (
        "Here are things you remember about the user that may be relevant. Use them "
        "naturally when helpful; don't recite them verbatim or mention that you're "
        f"recalling memory:\n{facts}"
    )


_graph = None


def get_graph():
    """Return the compiled graph, building it once (lazily, so import needs no key)."""
    global _graph
    if _graph is None:
        _graph = _build()
    return _graph


def _build():
    lc_tools = tools.langchain_tools()
    model = build_model().bind_tools(lc_tools)

    async def agent_node(state: State) -> dict:
        # System prompt + recalled memories are prepended per call, not stored in state.
        prompt: list = [SystemMessage(content=system_prompt())]
        recall = _recall_block(state["messages"])
        if recall:
            prompt.append(SystemMessage(content=recall))
        prompt.extend(state["messages"])
        response = await model.ainvoke(prompt)
        return {"messages": [response]}

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(lc_tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()
