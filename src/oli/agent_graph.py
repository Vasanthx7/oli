"""The agent as a LangGraph StateGraph.

Replaces the hand-rolled observe->think->act loop (see ADR 0002/0003). The graph is
the canonical ReAct shape:

    START -> agent -> (tool calls?) -> tools -> agent -> ... -> END

The `agent` node injects the personality prompt and any recalled memories at call
time (they live outside the persisted message state), binds the tools, and calls
the model. `tools_condition` routes to the ToolNode whenever the model requests a
tool, then back to the agent. The LLM is the resilient cloud chain (Groq -> Mistral
failover) built in :mod:`oli.providers` — see ADR 0006/0017.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from . import intent, memory, providers, tools
from .personality import system_prompt

# Safety rail: max agent<->tools laps before LangGraph aborts (was MAX_TOOL_ITERATIONS).
RECURSION_LIMIT = 16


class State(TypedDict, total=False):
    # messages is the only required channel; intent is populated by the classify node.
    messages: Annotated[list[AnyMessage], add_messages]
    intent: dict


def build_model(tier: str = "reasoning", *, tools: list | None = None) -> Runnable:
    """A tool-bound chat model over the cloud provider chain (Groq -> Mistral failover).

    ``tier`` picks the strong (``"reasoning"``) or cheap (``"fast"``) model id per
    provider; the returned Runnable tries the primary and fails over to the rest — see
    :mod:`oli.providers` and ADR 0017. ``stream_usage`` emits token usage on the final
    streamed chunk so ``run_turn`` can record prompt/completion metrics.
    """
    return providers.build_chat_model(tier, tools=tools, temperature=0.7, stream_usage=True)


def _use_reasoning_tier(intent: dict | None) -> bool:
    """Route to the strong tier for hard or tool-needing turns; fast tier otherwise.

    A missing/failed intent (None) routes to the strong tier — the safe default is to
    not under-power a turn we couldn't classify."""
    if not intent:
        return True
    return intent.get("complexity") == "hard" or bool(intent.get("needs_tools"))


async def _recall_block(state_messages: list) -> str | None:
    """Build a system block of memories relevant to the latest user message."""
    mem = memory.active()
    if mem is None:
        return None
    last_human = next((m for m in reversed(state_messages) if isinstance(m, HumanMessage)), None)
    if last_human is None:
        return None
    try:
        hits = await mem.recall(str(last_human.content))
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
    # Two tiers, bound once. Routing picks per turn from the classified intent.
    fast_model = build_model("fast", tools=lc_tools)
    reasoning_model = build_model("reasoning", tools=lc_tools)

    async def classify_node(state: State) -> dict:
        # Tag the turn with a structured intent (best-effort; never raises).
        last_human = next(
            (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
        )
        if last_human is None:
            return {}
        result = await intent.classify(str(last_human.content))
        return {"intent": result.model_dump()}

    async def agent_node(state: State) -> dict:
        # System prompt + recalled memories are prepended per call, not stored in state.
        prompt: list = [SystemMessage(content=system_prompt())]
        recall = await _recall_block(state["messages"])
        if recall:
            prompt.append(SystemMessage(content=recall))
        prompt.extend(state["messages"])
        # Route to the fast or strong tier based on this turn's intent.
        model = reasoning_model if _use_reasoning_tier(state.get("intent")) else fast_model
        response = await model.ainvoke(prompt)
        return {"messages": [response]}

    graph = StateGraph(State)
    graph.add_node("classify", classify_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(lc_tools))
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()
