"""Graded browse scenarios for evaluating local browse agents.

Shared across harnesses: the qwen2.5vl:7b browser-use baseline (``run.py``) and the
Fara / rival computer-use models (``run_fara.py`` and the multi-model runner). Same
scenarios + validators everywhere so cross-model comparison is apples-to-apples.


Each scenario is deliberately small and self-contained so a failure points at a
specific capability, not a tangle of them. Scenarios are graded by ``tier``:

    1  navigate + read a single obvious fact   (should pass ~always)
    2  extract several items / a derived fact   (reading comprehension)
    3  interact: search box, follow a result    (form fill + click)
    4  dynamic / multi-hop / JS-heavy           (the stress cases)

``validate`` receives the browse final-result string (lower-cased) and returns
True when the answer is acceptable. Keep validators permissive about phrasing
but strict about the fact — we're grading the browser agent, not its prose.

Targets are chosen to be stable, lightweight, and robots-friendly. Edit freely:
this file is the knob you turn to change what "the model can do" means.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    id: str
    tier: int
    goal: str
    validate: Callable[[str], bool]
    # Human note on what this probes and why it might fail.
    probes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def _contains_all(*needles: str) -> Callable[[str], bool]:
    lowered = [n.lower() for n in needles]
    return lambda out: all(n in out for n in lowered)


def _contains_any(*needles: str) -> Callable[[str], bool]:
    lowered = [n.lower() for n in needles]
    return lambda out: any(n in out for n in lowered)


SCENARIOS: list[Scenario] = [
    # ---- Tier 1: navigate + read a single obvious fact --------------------
    Scenario(
        id="t1-example-heading",
        # example.org, not .com: .com fails DNS on some networks (observed here),
        # while .org serves the identical "Example Domain" page.
        tier=1,
        goal="Go to https://example.org and report the exact main heading text on the page.",
        validate=_contains_all("example domain"),
        probes="Baseline: can it load one static page and read the one heading. "
        "If this fails, the problem is the browser/model wiring, not reasoning.",
        tags=("static", "read"),
    ),
    Scenario(
        id="t1-httpbin-html-title",
        tier=1,
        goal="Go to https://httpbin.org/html and report the name of the author "
        "mentioned in the text (it is a Moby Dick excerpt).",
        validate=_contains_any("melville", "herman"),
        probes="Read a fact from body text rather than a heading; small, stable page.",
        tags=("static", "read"),
    ),
    # ---- Tier 2: extract several items / a derived fact --------------------
    Scenario(
        id="t2-wikipedia-python-year",
        tier=2,
        goal="Go to https://en.wikipedia.org/wiki/Python_(programming_language) and "
        "report the year Python was first released.",
        validate=_contains_all("1991"),
        probes="Find one specific fact in a long article (infobox or intro). "
        "Tests scrolling/scanning past a lot of DOM.",
        tags=("read", "long-page"),
    ),
    Scenario(
        id="t2-books-catalogue-count",
        tier=2,
        goal="Go to https://books.toscrape.com and report how many books in total "
        "are in the catalogue (the site states the total count).",
        validate=_contains_all("1000"),
        probes="Read a specific number stated on a dense listing page ('1000 "
        "results'). Strict + stable, unlike volatile 'top stories' lists.",
        tags=("read", "extract"),
    ),
    Scenario(
        id="t2-quotes-first-author",
        tier=2,
        goal="Go to https://quotes.toscrape.com and report the author of the very "
        "first quote listed on the page.",
        validate=_contains_any("einstein"),
        probes="Associate a quote with its author (first quote is Albert Einstein). "
        "Tests reading a structured list item, not just a heading.",
        tags=("read", "extract"),
    ),
    # ---- Tier 3: interact (search box, follow a result) -------------------
    Scenario(
        id="t3-ddg-search-firstresult",
        tier=3,
        goal="Go to https://duckduckgo.com/html/, search for 'browser-use github', "
        "and report the URL (or domain) of the first result.",
        validate=_contains_any("github.com", "github"),
        probes="Type into a search box, submit, read the first result. Known-hard: "
        "the model looped here — kept as a discriminator between models.",
        tags=("form", "search", "click"),
    ),
    Scenario(
        id="t3-wikipedia-search",
        tier=3,
        goal="Go to https://en.wikipedia.org, use the search box to search for "
        "'Ada Lovelace', open the article, and report what she is best known for.",
        validate=_contains_any("mathematician", "computer", "programmer", "analytical engine"),
        probes="Search + navigate to a new page + read — a two-hop interaction.",
        tags=("form", "search", "multi-hop"),
    ),
    Scenario(
        id="t3-httpbin-form-submit",
        tier=3,
        goal="Go to https://httpbin.org/forms/post, fill the Customer name field "
        "with 'Ada' and the Customer comments field with 'browsereval42', then "
        "submit the order. Confirm success by reporting what the response shows.",
        # httpbin echoes the posted form as JSON, so a correct submit round-trips
        # the sentinel value back into the final page text — a deterministic check.
        validate=_contains_any("browsereval42"),
        probes="Multi-field form fill + submit + read the echoed response. A real "
        "interaction with a deterministic, non-volatile success signal.",
        tags=("form", "fill", "submit"),
    ),
    # ---- Tier 4: dynamic / multi-hop / JS-heavy (stress) -----------------
    Scenario(
        id="t4-books-home-firstprice",
        tier=4,
        goal="Go to https://books.toscrape.com and report the exact price (including "
        "the currency symbol) of the first book shown on the home page.",
        validate=_contains_all("51.77"),
        probes="Catalogue read tightened to the exact price of the first book "
        "(A Light in the Attic, £51.77) — no more 'contains a £' soft pass.",
        tags=("read", "ecommerce"),
    ),
    Scenario(
        id="t4-books-travel-multihop",
        tier=4,
        goal="Go to https://books.toscrape.com, open the 'Travel' category, open the "
        "first book listed in it, and report that book's title and price.",
        validate=lambda out: "himalayas" in out and "45.17" in out,
        probes="Home -> category -> product detail (3 hops) with two facts checked "
        "strictly (It's Only the Himalayas, £45.17). The e-commerce stress case.",
        tags=("multi-hop", "click", "ecommerce"),
    ),
]


def by_tier(max_tier: int | None = None, ids: list[str] | None = None) -> list[Scenario]:
    """Filter scenarios by a maximum tier and/or an explicit id allow-list."""
    out = SCENARIOS
    if ids:
        wanted = set(ids)
        out = [s for s in out if s.id in wanted]
    if max_tier is not None:
        out = [s for s in out if s.tier <= max_tier]
    return out
