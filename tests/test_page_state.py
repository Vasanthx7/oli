"""Offline unit tests for the deterministic cart reader (no Playwright needed).

The risky part of state-grounded verification is the parsing, so we exercise it with
canned ``raw`` dicts (the shape ``_CART_SCRAPE_JS`` returns) and a fake page whose async
``evaluate`` hands back those dicts — the same SimpleNamespace-fake pattern used in
tests/test_browse_handover.py. The live-DOM path is covered (gated) in test_live_browser.
"""

from __future__ import annotations

from types import SimpleNamespace

from oli.tools import page_state as ps

# --- pure helpers ---------------------------------------------------------------------


def test_parse_money():
    assert ps._parse_money("₹1,299.00") == 1299.0
    assert ps._parse_money("Rs 1299") == 1299.0
    assert ps._parse_money("Subtotal (3 items): ₹1,299.00") == 1299.0
    assert ps._parse_money("$12.50") == 12.5
    assert ps._parse_money("free") is None
    assert ps._parse_money("") is None
    assert ps._parse_money(None) is None


def test_parse_count():
    assert ps._parse_count("3") == 3
    assert ps._parse_count("3 items") == 3
    assert ps._parse_count("Cart (2)") == 2
    assert ps._parse_count("0") == 0
    assert ps._parse_count("cart") is None
    assert ps._parse_count("") is None
    assert ps._parse_count(None) is None


def test_site_for_host():
    assert ps.site_for_host("www.amazon.in") == "amazon"
    assert ps.site_for_host("www.zomato.com") == "zomato"
    # Guards the profiles._domain_root gotcha: next.cult.fit must NOT collapse to "fit".
    assert ps.site_for_host("next.cult.fit") == "cult"
    assert ps.site_for_host("example.org") == "generic"
    assert ps.site_for_host("") == "generic"


# --- parse_cart: per-site + generic ---------------------------------------------------


def test_parse_cart_amazon_empty():
    raw = {"empty": True, "cartCountTexts": [], "url": "https://www.amazon.in/cart"}
    cs = ps.parse_cart(raw, "amazon")
    assert cs.ok and cs.empty is True and cs.count == 0


def test_parse_cart_amazon_has_items():
    raw = {
        "cartCountTexts": ["3"],
        "subtotalTexts": ["Subtotal (3 items): ₹1,299.00"],
        "moneyTexts": ["₹1,299.00"],
    }
    cs = ps.parse_cart(raw, "amazon")
    assert cs.ok and cs.count == 3 and cs.subtotal == 1299.0 and cs.empty is False


def test_parse_cart_amazon_unknown_when_no_signals():
    cs = ps.parse_cart({"cartCountTexts": [], "subtotalTexts": [], "moneyTexts": []}, "amazon")
    assert cs.ok is False


def test_parse_cart_generic_requires_both_signals():
    # Count only → not trustworthy (a stray number must not trigger a downgrade).
    assert ps.parse_cart({"cartCountTexts": ["2"]}, "generic").ok is False
    # Money only → not trustworthy.
    assert ps.parse_cart({"moneyTexts": ["₹500"]}, "generic").ok is False
    # Both present → trustworthy.
    cs = ps.parse_cart({"cartCountTexts": ["2"], "moneyTexts": ["₹500"]}, "generic")
    assert cs.ok and cs.count == 2 and cs.subtotal == 500.0


def test_parse_cart_junk_is_unknown():
    # Drift safety: garbage in → ok=False (never a false contradiction).
    assert ps.parse_cart({"bodyText": "lorem ipsum"}, "amazon").ok is False
    assert ps.parse_cart({}, "generic").ok is False


def test_order_was_placed():
    assert ps.order_was_placed({"order": True}, "amazon") is True
    assert ps.order_was_placed({"order": False}, "amazon") is False
    assert ps.order_was_placed({}, "amazon") is False


# --- read_cart against a fake page -----------------------------------------------------


def _fake_page(url: str, evaluate_result):
    async def _evaluate(_js):
        if isinstance(evaluate_result, Exception):
            raise evaluate_result
        return evaluate_result

    return SimpleNamespace(url=url, evaluate=_evaluate)


async def test_read_cart_with_fake_page():
    raw = {"cartCountTexts": ["3"], "subtotalTexts": ["Subtotal: ₹1,299.00"]}
    page = _fake_page("https://www.amazon.in/gp/cart/view.html", raw)
    cs = await ps.read_cart(page)
    assert cs.site == "amazon" and cs.ok and cs.count == 3 and cs.subtotal == 1299.0


async def test_read_cart_eval_failure_is_unknown():
    page = _fake_page("https://www.amazon.in/", RuntimeError("page closed"))
    cs = await ps.read_cart(page)
    assert cs.ok is False and cs.site == "amazon"


async def test_read_cart_non_dict_is_unknown():
    page = _fake_page("https://www.amazon.in/", "not-a-dict")
    cs = await ps.read_cart(page)
    assert cs.ok is False
