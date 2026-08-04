"""_apply_location_override tests — SPEC §4.1 (plan_searches location-override path).

Pure/deterministic: no network, no LLM. Regression coverage for a real bug: broaden=True
(the frontend default) used to leave every planner-nationwide query nationwide, silently
discarding the user's chosen cities for most of the plan.
"""

from typing import Any

import pytest

from graph_harness import _initial_state, _profile
from jdparser.config import SEARCH_PLAN_MAX_QUERIES
from jdparser.graph.nodes import _apply_location_override
from jdparser.llm.schemas import AdzunaQuery, ResumeProfile


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


# --- plan_searches integration: the override as the graph applies it ----------
def test_search_locations_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan_searches replaces the profile's inferred locations with the per-run
    override and updates the run profile (so the judge sees the chosen locations);
    a None override leaves the inferred locations untouched."""
    from jdparser.graph import nodes

    captured: dict[str, list[str]] = {}

    def _capture(profile: ResumeProfile) -> list[AdzunaQuery]:
        captured["locations"] = list(profile.locations)
        return [AdzunaQuery(what="engineer")]

    monkeypatch.setattr("jdparser.graph.nodes.plan_queries", _capture)

    state = _initial_state([])
    state["resume_profile"] = _profile().model_dump()
    state["search_locations"] = ["New York", "Remote"]
    out = nodes.plan_searches(state)
    assert captured["locations"] == ["New York", "Remote"]              # override applied
    assert out["resume_profile"]["locations"] == ["New York", "Remote"]  # judge sees them too

    captured.clear()
    state["search_locations"] = None
    out2 = nodes.plan_searches(state)
    assert captured["locations"] == ["Austin, TX"]   # inferred used
    assert "resume_profile" not in out2              # not overwritten when no override


def test_location_override_used_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit location override is applied VERBATIM as `where` (a state like "TX" is
    NOT narrowed to a city) to EVERY query — including ones the planner itself already
    left nationwide (regression: broaden=True used to leave those nationwide untouched,
    silently ignoring the override for most of the plan) — distance is cleared, and
    broaden adds exactly one extra nationwide query on top."""
    from jdparser.graph import nodes

    def _planner(profile: ResumeProfile) -> list[AdzunaQuery]:
        # planner "normalizes" to a city + emits a nationwide query
        return [
            AdzunaQuery(what="qa", where="Austin, TX", distance=30),
            AdzunaQuery(what="qa analyst", where="Austin, TX"),
            AdzunaQuery(what="software tester"),  # nationwide
        ]

    monkeypatch.setattr("jdparser.graph.nodes.plan_queries", _planner)
    state = _initial_state([])
    state["resume_profile"] = _profile().model_dump()

    state["search_locations"] = ["TX"]
    state["broaden_search"] = True
    plan = nodes.plan_searches(state)["search_plan"]
    # every original query scoped to "TX" verbatim (including the planner's nationwide
    # one), plus one additional nationwide query appended for breadth
    assert [q["where"] for q in plan] == ["TX", "TX", "TX", None]
    assert all(q.get("distance") is None for q in plan)          # radius cleared for statewide

    state["broaden_search"] = False                              # strict: no nationwide
    plan2 = nodes.plan_searches(state)["search_plan"]
    assert [q["where"] for q in plan2] == ["TX", "TX", "TX"]


def test_max_days_old_applied_to_every_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listing-age filter is stamped on every query; 0/None clears it (any age)."""
    from jdparser.graph import nodes

    monkeypatch.setattr(
        "jdparser.graph.nodes.plan_queries",
        lambda p: [AdzunaQuery(what="qa", max_days_old=99), AdzunaQuery(what="qa analyst")],
    )
    state = _initial_state([])
    state["resume_profile"] = _profile().model_dump()

    state["max_days_old"] = 7
    plan = nodes.plan_searches(state)["search_plan"]
    assert [q["max_days_old"] for q in plan] == [7, 7]          # stamped on all, overrides planner's 99

    state["max_days_old"] = 0                                   # "any age" -> cleared everywhere
    plan2 = nodes.plan_searches(state)["search_plan"]
    assert [q["max_days_old"] for q in plan2] == [None, None]


def test_location_override_remote_is_nationwide(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purely-remote override searches nationwide (no bogus where="Remote")."""
    from jdparser.graph import nodes

    monkeypatch.setattr(
        "jdparser.graph.nodes.plan_queries",
        lambda p: [AdzunaQuery(what="qa", where="Austin, TX"), AdzunaQuery(what="qa analyst")],
    )
    state = _initial_state([])
    state["resume_profile"] = _profile().model_dump()
    state["search_locations"] = ["Remote"]
    plan = nodes.plan_searches(state)["search_plan"]
    assert [q["where"] for q in plan] == [None, None]           # all nationwide


def test_broaden_search_strict_drops_nationwide(monkeypatch: pytest.MonkeyPatch) -> None:
    """broaden_search=False drops the planner's nationwide (where-less) queries so the
    search stays within the chosen locations; broaden_search=True keeps them."""
    from jdparser.graph import nodes

    def _planner(profile: ResumeProfile) -> list[AdzunaQuery]:
        return [AdzunaQuery(what="engineer", where="Seattle, WA"), AdzunaQuery(what="engineer")]

    monkeypatch.setattr("jdparser.graph.nodes.plan_queries", _planner)
    state = _initial_state([])
    state["resume_profile"] = _profile().model_dump()

    state["broaden_search"] = True
    out = nodes.plan_searches(state)
    assert [q.get("where") for q in out["search_plan"]] == ["Seattle, WA", None]

    state["broaden_search"] = False
    out2 = nodes.plan_searches(state)
    assert [q.get("where") for q in out2["search_plan"]] == ["Seattle, WA"]  # nationwide dropped
