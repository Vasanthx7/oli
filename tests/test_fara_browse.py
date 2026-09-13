"""Offline unit tests for the Fara browse engine's pure helpers.

No browser, no network — just the coordinate math, response parsing, and image
trimming that decide whether the loop behaves. (The full loop is exercised
manually against a live Ollama; these lock the fiddly bits.)
"""

from __future__ import annotations

from oli.tools import fara_browse as fb


def test_to_px_scales_0_1000_to_viewport():
    assert fb._to_px([0, 0]) == (0, 0)
    assert fb._to_px([1000, 1000]) == (fb.VIEWPORT["width"], fb.VIEWPORT["height"])
    assert fb._to_px([500, 500]) == (720, 450)
    # Defensive: a missing/empty coordinate maps to the origin, never crashes.
    assert fb._to_px(None) == (0, 0)


def test_parse_extracts_action_and_coordinate():
    msg = (
        "I should click the button.\n"
        '<tool_call>\n{"name": "computer_use", "arguments": '
        '{"action": "left_click", "coordinate": [100, 200]}}\n</tool_call>'
    )
    thoughts, args = fb._parse(msg)
    assert args["action"] == "left_click"
    assert args["coordinate"] == [100, 200]
    assert args["thoughts"].startswith("I should click")


def test_parse_terminate_answer():
    msg = (
        "Found it.\n<tool_call>\n"
        '{"name": "computer_use", "arguments": {"action": "terminate", "answer": "1991"}}'
        "\n</tool_call>"
    )
    _, args = fb._parse(msg)
    assert args["action"] == "terminate"
    assert args["answer"] == "1991"


def test_parse_unparseable_falls_back_to_terminate():
    # No tool_call block -> end the run with the raw text as the answer, don't raise.
    _, args = fb._parse("just some prose, no action here")
    assert args["action"] == "terminate"
    assert "just some prose" in args["answer"]


def test_trim_images_keeps_only_most_recent():
    def img_msg(tag: str) -> dict:
        return {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": tag}},
                {"type": "text", "text": tag},
            ],
        }

    messages = [{"role": "system", "content": "sys"}] + [img_msg(f"s{i}") for i in range(5)]
    fb._trim_images(messages, keep=3)
    # Exactly the 3 newest image messages still carry an image part.
    with_img = [
        m
        for m in messages
        if isinstance(m["content"], list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]
    assert len(with_img) == 3
    # Older messages keep their text, just lose the screenshot.
    assert any(
        isinstance(m["content"], list) and m["content"] and m["content"][0].get("text") == "s0"
        for m in messages
    )


def test_system_prompt_vendored_verbatim():
    # The card says use the trained prompt as-is; make sure it's present and intact.
    assert "Fara" in fb._SYSTEM_PROMPT
    assert '"enum"' in fb._SYSTEM_PROMPT  # the computer_use action schema
    assert len(fb._SYSTEM_PROMPT) > 5000
