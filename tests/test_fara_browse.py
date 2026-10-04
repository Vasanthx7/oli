"""Offline unit tests for the Fara browse engine's pure helpers.

No browser, no network — just the coordinate math, response parsing, and image
trimming that decide whether the loop behaves. (The full loop is exercised
manually against a live Ollama; these lock the fiddly bits.)
"""

from __future__ import annotations

from types import SimpleNamespace

from oli.tools import fara_browse as fb
from oli.tools import page_state as ps


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


def test_route_model_sends_interaction_and_dense_to_heavy():
    light = fb.config.settings.fara_model
    heavy = fb.config.settings.fara_model_heavy
    assert light != heavy  # sanity: two distinct tiers configured
    # Clean read/nav/extract -> fast tier.
    assert fb._route_model("Go to example.org and report the heading")[0] == light
    assert fb._route_model("Tell me when Python was first released on Wikipedia")[0] == light
    # Interaction verbs -> careful tier.
    assert fb._route_model("search for a cable and add it to my cart")[0] == heavy
    assert fb._route_model("fill the form and submit it")[0] == heavy
    # Dense/bot-heavy domain -> careful tier even without an obvious verb.
    assert fb._route_model("open amazon.in and find the cheapest usb-c cable")[0] == heavy


def test_route_model_respects_autoroute_off(monkeypatch):
    monkeypatch.setattr(fb.config.settings, "fara_autoroute", False)
    # With autoroute off, everything uses the base model.
    assert fb._route_model("add it to my cart on amazon.in")[0] == fb.config.settings.fara_model


def test_system_prompt_vendored_verbatim():
    # The card says use the trained prompt as-is; make sure it's present and intact.
    assert "Fara" in fb._SYSTEM_PROMPT
    assert '"enum"' in fb._SYSTEM_PROMPT  # the computer_use action schema
    assert len(fb._SYSTEM_PROMPT) > 5000


# --- State-grounded verification (ADR 0020) -------------------------------------------


def test_is_cart_goal():
    assert fb._is_cart_goal("add the cheapest cable to my cart")
    assert fb._is_cart_goal("check my subtotal")
    assert fb._is_cart_goal("reorder my last order")
    assert not fb._is_cart_goal("report the main heading on example.org")
    assert not fb._is_cart_goal("what classes am I booked into this week")


def test_claims_cart_success():
    assert fb._claims_cart_success("Added to your cart, subtotal ₹499.")
    assert fb._claims_cart_success("The item is now in the cart.")
    # Failure words veto a success read.
    assert not fb._claims_cart_success("I could not add it — the cart is empty.")
    assert not fb._claims_cart_success("You appear to be signed out, so nothing was added.")
    assert not fb._claims_cart_success("Here is the weather today.")


def test_reconcile_downgrades_empty_cart():
    cs = ps.CartState(site="amazon", count=0, empty=True, ok=True)
    out = fb._reconcile_answer("Added the cable to your cart.", "add a cable to my cart", cs)
    assert "NOT added" in out and "EMPTY" in out
    assert out.startswith("Added the cable")  # original preserved, warning appended


def test_reconcile_keeps_corroborated():
    cs = ps.CartState(site="amazon", count=2, subtotal=999.0, empty=False, ok=True)
    ans = "Added the cable; your cart now has 2 items."
    assert fb._reconcile_answer(ans, "add a cable to my cart", cs) == ans


def test_reconcile_only_item_mismatch():
    cs = ps.CartState(site="amazon", count=3, empty=False, ok=True)
    out = fb._reconcile_answer(
        "Done — it's the only item in your cart.",
        "add the latest iphone, make sure it is the only item in the cart",
        cs,
    )
    assert "actually contains 3 items" in out


def test_reconcile_order_placed_on_stop_goal():
    cs = ps.CartState(site="amazon", count=1, ok=True, raw={"order": True})
    out = fb._reconcile_answer(
        "Placed the order.", "add a cable to cart but stop before placing the order", cs
    )
    assert "order may have been PLACED" in out


async def test_verify_terminal_downgrades_hallucinated_add(monkeypatch):
    monkeypatch.setattr(fb.config.settings, "fara_verify_state", True)

    async def _fake_read(_page):
        return ps.CartState(site="amazon", count=0, empty=True, ok=True)

    monkeypatch.setattr(ps, "read_cart", _fake_read)
    st = SimpleNamespace(goal="add a cable to my cart", page=object())
    out = await fb._verify_terminal(st, "Added the cable to your cart.")
    assert "NOT added" in out


async def test_verify_terminal_noop_when_flag_off(monkeypatch):
    monkeypatch.setattr(fb.config.settings, "fara_verify_state", False)
    st = SimpleNamespace(goal="add a cable to my cart", page=object())
    ans = "Added the cable to your cart."
    assert await fb._verify_terminal(st, ans) == ans


async def test_verify_terminal_noop_on_non_cart_goal(monkeypatch):
    monkeypatch.setattr(fb.config.settings, "fara_verify_state", True)
    st = SimpleNamespace(goal="report the heading on example.org", page=object())
    ans = "The heading is Example Domain."
    assert await fb._verify_terminal(st, ans) == ans


async def test_verify_terminal_keeps_answer_when_read_unknown(monkeypatch):
    monkeypatch.setattr(fb.config.settings, "fara_verify_state", True)

    async def _fake_read(_page):
        return ps.CartState(site="amazon", ok=False)

    monkeypatch.setattr(ps, "read_cart", _fake_read)
    st = SimpleNamespace(goal="add a cable to my cart", page=object())
    ans = "Added the cable to your cart."
    assert await fb._verify_terminal(st, ans) == ans
