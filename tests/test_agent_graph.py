"""Tests for the LangGraph agent wiring (no network — graph build only)."""

from oli import tools
from oli.agent_graph import _use_reasoning_tier, get_graph


def test_langchain_tools_have_expected_names_and_args():
    ts = {t.name: set(t.args) for t in tools.langchain_tools()}
    assert ts["web_search"] == {"query"}
    assert ts["web_fetch"] == {"url"}
    assert ts["browse"] == {"goal", "profile"}  # profile is optional (logged-in sessions)
    assert ts["remember"] == {"content"}
    assert ts["recall_memory"] == {"query"}


def test_graph_builds_with_react_shape():
    graph = get_graph()
    nodes = set(graph.get_graph().nodes)
    # classify runs before the agent; the agent<->tools ReAct loop follows.
    assert {"classify", "agent", "tools"} <= nodes
    # cached across calls
    assert get_graph() is graph


def test_routing_picks_tier_by_intent():
    # Fast tier only for a simple turn that needs no tools.
    assert _use_reasoning_tier({"complexity": "simple", "needs_tools": False}) is False
    # Strong tier for hard, or tool-needing, or unclassifiable turns.
    assert _use_reasoning_tier({"complexity": "hard", "needs_tools": False}) is True
    assert _use_reasoning_tier({"complexity": "simple", "needs_tools": True}) is True
    assert _use_reasoning_tier(None) is True
