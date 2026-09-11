"""Tests for the LangGraph agent wiring (no network — graph build only)."""

from oli import tools
from oli.agent_graph import get_graph


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
