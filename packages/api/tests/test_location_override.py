"""_apply_location_override tests — SPEC §4.1 (plan_searches location-override path).

Pure/deterministic: no network, no LLM. Regression coverage for a real bug: broaden=True
(the frontend default) used to leave every planner-nationwide query nationwide, silently
discarding the user's chosen cities for most of the plan.
"""

from jdparser.config import SEARCH_PLAN_MAX_QUERIES
from jdparser.graph.nodes import _apply_location_override
from jdparser.llm.schemas import AdzunaQuery


def _plan(n: int, scoped_indices: set[int] = frozenset()) -> list[AdzunaQuery]:
    """`n` planner queries; queries at `scoped_indices` come pre-scoped to a city the
    planner itself picked (as the real planner sometimes does)."""
    return [
        AdzunaQuery(what=f"role {i}", where="Chicago, IL" if i in scoped_indices else None)
        for i in range(n)
    ]


def test_broaden_scopes_every_query_plus_one_nationwide() -> None:
    """Every query gets one of the override cities (cycled) — not just the ones the
    planner happened to leave where-scoped — plus exactly one nationwide query added."""
    locations = ["Austin, TX", "Dallas, TX", "Houston, TX"]
    plan = _plan(4, scoped_indices={0})  # planner scoped query 0 to Chicago on its own
    out = _apply_location_override(plan, locations, broaden=True)

    geo_scoped = [q for q in out if q.where is not None]
    nationwide = [q for q in out if q.where is None]
    assert len(nationwide) == 1
    assert all(q.where in locations for q in geo_scoped)
    # cycling: query i -> locations[i % 3] for every ORIGINAL query, not just query 0
    assert [q.where for q in out[:4]] == ["Austin, TX", "Dallas, TX", "Houston, TX", "Austin, TX"]


def test_strict_scopes_every_query_no_nationwide() -> None:
    """broaden=False (strict): every query geo-scoped, no nationwide query added."""
    locations = ["Austin, TX", "Dallas, TX"]
    plan = _plan(3)
    out = _apply_location_override(plan, locations, broaden=False)
    assert all(q.where is not None for q in out)
    assert [q.where for q in out] == ["Austin, TX", "Dallas, TX", "Austin, TX"]


def test_remote_in_locations_forces_nationwide_even_when_strict() -> None:
    """A remote/non-geo value in the override adds a nationwide query even with
    broaden=False (has_remote overrides strict)."""
    locations = ["Austin, TX", "Remote"]
    plan = _plan(3)
    out = _apply_location_override(plan, locations, broaden=False)
    assert any(q.where is None for q in out)
    assert all(q.where in (None, "Austin, TX") for q in out)


def test_purely_remote_override_makes_everything_nationwide() -> None:
    plan = _plan(3)
    out = _apply_location_override(plan, ["Remote", "Anywhere"], broaden=True)
    assert all(q.where is None and q.distance is None for q in out)


def test_output_capped_at_max_queries() -> None:
    plan = _plan(SEARCH_PLAN_MAX_QUERIES + 3)
    out = _apply_location_override(plan, ["Austin, TX"], broaden=True)
    assert len(out) == SEARCH_PLAN_MAX_QUERIES
