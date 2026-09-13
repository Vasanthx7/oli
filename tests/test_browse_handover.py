"""Resumable browse handover (ask_user_question pauses, the user's reply resumes).

Offline: a fake OpenAI client scripts the Fara responses and the browser teardown /
resume-seed are stubbed, so we exercise the pause/resume state machine and the run_turn
routing without a real Chromium or model. See oli.tools.fara_browse + agent._resume_browse_turn.
"""

from types import SimpleNamespace

import pytest

from oli import agent
from oli.storage import Storage
from oli.tools import fara_browse as fb


def _resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _tool_call(action: str, **args: object) -> str:
    import json

    payload = {"name": "computer_use", "arguments": {"action": action, **args}}
    return f"thinking\n<tool_call>\n{json.dumps(payload)}\n</tool_call>"


class _ScriptedClient:
    """Returns queued responses in order from chat.completions.create."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *a: object, **k: object) -> SimpleNamespace:
        return _resp(self._responses.pop(0))


def _state(responses: list[str]) -> fb._RunState:
    return fb._RunState(
        client=_ScriptedClient(responses),  # type: ignore[arg-type]
        model_tag="fara15-4b",
        pw=None,
        browser=None,
        page=SimpleNamespace(url="https://cult.fit"),
        live=SimpleNamespace(),
        attached=False,
        agent=fb.FaraBrowseAgent(),
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"}],
        trace_dir=None,
    )


@pytest.fixture(autouse=True)
def _reset_paused(monkeypatch):
    # No real browser: stub teardown and the resume-seed screenshot.
    monkeypatch.setattr(fb, "_paused", None, raising=False)
    fb._clear_handover()  # isolate the on-disk persistence between tests

    async def _noop_close(_st):
        return None

    async def _fake_reply(st, reply):
        st.messages.append({"role": "user", "content": reply})

    monkeypatch.setattr(fb, "_close", _noop_close)
    monkeypatch.setattr(fb, "_append_user_reply", _fake_reply)
    yield
    fb._paused = None
    fb._clear_handover()


async def test_pump_pauses_on_ask_and_keeps_browser():
    st = _state([_tool_call("ask_user_question", question="Which cult.fit center?")])
    result = await fb._pump(st)
    assert fb.has_paused_browse() is True  # browser kept open, run stashed
    assert "which cult.fit center" in result.lower()


async def test_resume_continues_to_completion():
    st = _state(
        [
            _tool_call("ask_user_question", question="Which center?"),
            _tool_call("terminate", answer="Booked a 7am yoga class at Indiranagar."),
        ]
    )
    q = await fb._pump(st)
    assert fb.has_paused_browse() and "center" in q.lower()

    answer = await fb.resume_paused_browse("Indiranagar, 7am")
    assert fb.has_paused_browse() is False  # finished → browser torn down, nothing paused
    assert "indiranagar" in answer.lower()


async def test_resume_can_pause_again():
    st = _state(
        [
            _tool_call("ask_user_question", question="Which center?"),
            _tool_call("ask_user_question", question="What time?"),
        ]
    )
    await fb._pump(st)
    second = await fb.resume_paused_browse("Indiranagar")
    assert fb.has_paused_browse() is True  # asked again → still paused for the next reply
    assert "what time" in second.lower()


async def test_resume_with_nothing_paused_is_graceful():
    fb._paused = None
    msg = await fb.resume_paused_browse("hello")
    assert "no browsing task" in msg.lower()


async def test_run_turn_routes_reply_to_resume(monkeypatch):
    # A pending browse → the next user turn resumes it instead of hitting the graph.
    monkeypatch.setattr(fb, "has_pending_browse", lambda: True)

    async def _fake_resume(reply: str) -> str:
        return f"Resumed with: {reply}. Anything else?"

    monkeypatch.setattr(fb, "resume_pending_browse", _fake_resume)
    # If the graph were used this would blow up — assert it is NOT called.
    monkeypatch.setattr(
        agent, "get_graph", lambda: (_ for _ in ()).throw(AssertionError("graph used"))
    )

    store = Storage()
    cid = await store.create_conversation()
    out = [ev async for ev in agent.run_turn(store, cid, "Indiranagar, 7am")]

    kinds = [e["type"] for e in out]
    assert "tool_start" in kinds and "tool_end" in kinds and "done" in kinds
    done = next(e for e in out if e["type"] == "done")
    assert "resumed with: indiranagar, 7am" in done["content"].lower()

    # Persisted: the user reply + the browse tool row + the assistant answer.
    roles = [m["role"] for m in await store.get_messages(cid)]
    assert roles == ["user", "tool", "assistant"]


# --- cross-restart persistence -------------------------------------------------------


async def test_pause_persists_handover_to_disk():
    st = _state([_tool_call("ask_user_question", question="Which center?")])
    await fb._pump(st)
    # While the live run is in memory, has_persisted_handover is False (only true once the
    # live session is gone), but the record IS on disk with the goal + question.
    assert fb.has_persisted_handover() is False
    rec = fb._load_handover()
    assert rec is not None and rec["question"] == "Which center?"


async def test_warm_relaunch_after_restart(monkeypatch):
    # Simulate a restart: a record on disk, no in-memory paused run.
    st = _state([_tool_call("ask_user_question", question="Which site?")])
    st.goal = "Sign me up for the newsletter."
    await fb._pump(st)
    fb._paused = None  # the live run is gone (process restarted)

    assert fb.has_persisted_handover() is True
    assert fb.has_pending_browse() is True

    captured = {}

    async def _fake_browse(goal, profile=None, start_url=None):
        captured["goal"] = goal
        return "Relaunched and subscribed."

    monkeypatch.setattr(fb, "browse_fara", _fake_browse)
    result = await fb.resume_pending_browse("example.com, test@example.com")

    assert "relaunched" in result.lower()
    # The warm relaunch carries the original goal AND the user's reply into a fresh browse.
    assert "newsletter" in captured["goal"].lower()
    assert "test@example.com" in captured["goal"].lower()
    # Record consumed → nothing pending afterwards.
    assert fb.has_pending_browse() is False


async def test_resume_error_clears_persisted_record():
    # Pause persists a record; if the resume then errors (model drops), the run must clear
    # it — otherwise a stale handover would hijack the user's next unrelated message.
    st = _state([_tool_call("ask_user_question", question="Which center?")])
    await fb._pump(st)
    assert fb.has_pending_browse() is True
    r = await fb.resume_paused_browse("Indiranagar")  # no scripted response left -> errors
    assert fb.has_pending_browse() is False
    assert "offline" in r.lower() or "failed" in r.lower()


async def test_discard_clears_persisted_record():
    st = _state([_tool_call("ask_user_question", question="Which center?")])
    await fb._pump(st)
    assert fb._load_handover() is not None
    await fb.discard_paused_browse()
    assert fb._load_handover() is None
    assert fb.has_pending_browse() is False
