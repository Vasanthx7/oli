"""Deterministic page-state reader for `browse` verification.

The `browse` loop judges success by the model's self-reported text — so Fara will
happily claim *"added to cart"* when the live cart is empty (e.g. the session quietly
signed out). This module reads the **real** cart state straight from the DOM with
Playwright JS — no model, no cloud (preserves ADR 0016's local-only stance) — so the
loop can downgrade a false claim and the eval harness can grade on truth. See ADR 0020
(the Fara-vs-Holo A/B that put reliability on the roadmap) and the plan that birthed it.

Design contract — **advisory only**:

* The reader can only ever *catch a false success*. If anything is uncertain it returns
  ``ok=False`` and callers leave the model's answer untouched, so selector drift or an
  unfamiliar site degrades to a silent no-op — it must never fail a genuinely-correct run.
* Structure is **scrape-then-parse**: one JS string (`_CART_SCRAPE_JS`) gathers candidate
  signals into a plain dict; pure Python (`parse_cart`) decides. The risky parsing is thus
  unit-testable offline with canned dicts — no browser needed.
* Site keying uses an **explicit host-substring map** (`site_for_host`), NOT
  ``profiles._domain_root`` — that reduces ``next.cult.fit`` to ``"fit"`` (it doesn't know
  the ``fit`` TLD), which would mis-key cult.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class CartState:
    """A reading of the live cart. ``ok`` is the trust gate — False means 'unknown'."""

    site: str  # "amazon" | "zomato" | "cult" | "generic"
    count: int | None = None  # item count; 0 = empty, None = unknown
    subtotal: float | None = None  # numeric subtotal; None = unknown
    currency: str | None = None  # "₹" / "Rs" / "$" / … if detected
    empty: bool | None = None  # True/False when known, None = couldn't tell
    ok: bool = False  # True only when we read SOMETHING trustworthy
    url: str = ""
    raw: dict = field(default_factory=dict)  # scraped signals (tracing / tests)

    def as_dict(self) -> dict:
        """Flat dict for the eval ``state_out`` side channel / ``validate_state``."""
        return asdict(self)


# One JS arrow fn: scrape many candidate signals into a plain dict. Gathers generously so
# the Python side is *deciding*, not scraping. Must never throw — everything is wrapped and
# a partial dict is always returned.
_CART_SCRAPE_JS = r"""() => {
  const out = {hostname: "", url: "", cartCountTexts: [], subtotalTexts: [],
               moneyTexts: [], empty: false, order: false, bodyText: ""};
  try {
    out.hostname = location.hostname || "";
    out.url = location.href || "";
    const body = (document.body && document.body.innerText) || "";
    out.bodyText = body.slice(0, 4000);
    const low = body.toLowerCase();

    // Empty-cart / empty-bag copy (site-agnostic phrasings).
    out.empty = ["cart is empty", "bag is empty", "basket is empty",
                 "no items in your cart", "your cart is empty",
                 "there are no items"].some(p => low.includes(p));

    // Order-confirmation markers: URL path OR unambiguous body copy.
    const path = (location.pathname || "").toLowerCase();
    out.order = ["/thankyou", "/order-confirmation", "/orderconfirmation",
                 "/buy/thankyou"].some(p => path.includes(p))
      || ["order placed", "thank you for your order", "order confirmed",
          "your order has been placed"].some(p => low.includes(p));

    const txt = (el) => (el && (el.innerText || el.textContent) || "").trim();

    // Cart-count candidates: Amazon's badge + anything that looks like a cart counter.
    const countSel = ["#nav-cart-count", "[data-cart-count]", "[aria-label*='cart' i]",
                      "[class*='cart' i][class*='count' i]", "[class*='badge' i]",
                      "[class*='bag' i][class*='count' i]"];
    for (const s of countSel) {
      for (const el of document.querySelectorAll(s)) {
        const t = txt(el) || (el.getAttribute && el.getAttribute("data-cart-count")) || "";
        if (t) out.cartCountTexts.push(t);
        if (out.cartCountTexts.length >= 12) break;
      }
    }

    // Subtotal/total candidates: elements whose own text mentions subtotal/total/item total.
    const all = document.querySelectorAll("span, div, td, p, strong, b, h2, h3");
    let n = 0;
    for (const el of all) {
      if (n >= 4000) break; n++;
      const t = txt(el);
      if (!t || t.length > 120) continue;
      const tl = t.toLowerCase();
      if (/(sub\s*total|subtotal|item total|order total|grand total|^total\b)/.test(tl)) {
        out.subtotalTexts.push(t);
        if (out.subtotalTexts.length >= 12) break;
      }
    }

    // Loose money candidates anywhere (bounded) for the generic fallback.
    const money = body.match(/(?:₹|rs\.?|inr|\$|£|€)\s?\d[\d,]*(?:\.\d{1,2})?/gi) || [];
    out.moneyTexts = money.slice(0, 20);
  } catch (e) { /* always return a (partial) dict */ }
  return out;
}"""

# Match a currency figure: a symbol/word is REQUIRED, then the number. Requiring the
# symbol is deliberate — subtotal labels like "Subtotal (3 items): ₹1,299.00" contain a
# bare item count that must not win over the actual amount.
_MONEY_RE = re.compile(r"(₹|rs\.?|inr|\$|£|€)\s?(\d[\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"(₹|rs\.?|inr|\$|£|€)", re.IGNORECASE)


def _parse_money(s: str | None) -> float | None:
    """'₹1,299.00' -> 1299.0, 'Rs 1299' -> 1299.0; no-currency/'free'/'' -> None."""
    if not s:
        return None
    m = _MONEY_RE.search(str(s))
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _parse_count(s: str | None) -> int | None:
    """'3' / '3 items' / 'Cart (2)' -> int of the first integer; no digits -> None."""
    if s is None:
        return None
    m = re.search(r"-?\d+", str(s))
    return int(m.group()) if m else None


def _currency_of(s: str | None) -> str | None:
    if not s:
        return None
    m = _CURRENCY_RE.search(str(s))
    return m.group(1) if m else None


def site_for_host(host: str) -> str:
    """Explicit host-substring map (deliberately NOT profiles._domain_root)."""
    h = (host or "").lower()
    if "amazon" in h:
        return "amazon"
    if "zomato" in h:
        return "zomato"
    if "cult.fit" in h or "cultfit" in h:
        return "cult"
    return "generic"


def _first(seq: list, parser: Any) -> Any:
    for item in seq or []:
        val = parser(item)
        if val is not None:
            return val
    return None


def _subtotal_from(raw: dict) -> tuple[float | None, str | None]:
    """Prefer a figure from a subtotal-labelled node; fall back to any money text."""
    subs = raw.get("subtotalTexts") or []
    money = raw.get("moneyTexts") or []
    val = _first(subs, _parse_money)
    cur = _first(subs, _currency_of)
    if val is None:
        val = _first(money, _parse_money)
        cur = _first(money, _currency_of)
    return val, cur


def parse_cart(raw: dict, site: str) -> CartState:
    """PURE: decide a CartState from scraped signals. ``ok=False`` on any doubt."""
    raw = raw or {}
    url = str(raw.get("url", ""))

    # Empty-cart copy is a trustworthy, unambiguous reading → count 0.
    if raw.get("empty"):
        return CartState(site=site, count=0, empty=True, ok=True, url=url, raw=raw)

    count = _first(raw.get("cartCountTexts") or [], _parse_count)
    subtotal, currency = _subtotal_from(raw)

    if site == "generic":
        # Conservative: only trust the generic read when BOTH a count and a money figure
        # are present — a page that merely contains a number must not trigger a downgrade.
        if count is not None and subtotal is not None:
            return CartState(
                site=site,
                count=count,
                subtotal=subtotal,
                currency=currency,
                empty=(count == 0),
                ok=True,
                url=url,
                raw=raw,
            )
        return CartState(site=site, ok=False, url=url, raw=raw)

    # Per-site (amazon/zomato/cult): a readable count OR subtotal is enough to trust.
    if count is None and subtotal is None:
        return CartState(site=site, ok=False, url=url, raw=raw)
    return CartState(
        site=site,
        count=count,
        subtotal=subtotal,
        currency=currency,
        empty=(count == 0) if count is not None else None,
        ok=True,
        url=url,
        raw=raw,
    )


async def read_cart(page: Any) -> CartState:
    """Read the live cart off ``page``. Never raises → ``ok=False`` on any failure."""
    url = ""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    site = site_for_host(urlparse(url).netloc if url else "")
    try:
        raw = await page.evaluate(_CART_SCRAPE_JS)
    except Exception:  # noqa: BLE001 — page gone / eval blocked → unknown, not a failure
        return CartState(site=site, ok=False, url=url, raw={})
    if not isinstance(raw, dict):
        return CartState(site=site, ok=False, url=url, raw={})
    raw.setdefault("url", url)
    return parse_cart(raw, site)


def order_was_placed(raw: dict, site: str) -> bool:
    """Conservative: True only on an unambiguous order-confirmation marker."""
    return bool((raw or {}).get("order"))
