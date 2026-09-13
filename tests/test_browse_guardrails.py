"""Offline tests for browse's purchase guardrail (#4)."""

from __future__ import annotations

from oli.tools import browse


def test_purchase_terms_gated():
    for g in [
        "go to amazon and place the order",
        "checkout and pay for the items in my cart",
        "buy now the first result",
        "proceed to pay",
    ]:
        assert browse._needs_purchase_confirm(g), g


def test_shopping_not_gated():
    # "buy a cable" / "add to cart" are shopping, not the irreversible final step.
    for g in [
        "buy a usb-c cable on amazon",  # colloquial 'buy' == shop for
        "add a usb-c cable to my amazon cart",
        "search amazon for a cheap cable and open the first result",
    ]:
        assert not browse._needs_purchase_confirm(g), g
