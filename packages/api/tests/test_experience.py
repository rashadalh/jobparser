"""Deterministic years-of-experience (interval union) — overlap- and gap-correct."""

from jdparser.experience import total_years_experience
from jdparser.llm.schemas import WorkPeriod

NOW = 2026.0  # fixed "current year" so ongoing-role tests are deterministic


def _wp(start: float, end: float | None) -> WorkPeriod:
    return WorkPeriod(title="Engineer", organization="Acme", start_year=start, end_year=end)


def test_empty() -> None:
    assert total_years_experience([], NOW) == 0.0


def test_single_closed_role() -> None:
    assert total_years_experience([_wp(2016, 2024)], NOW) == 8.0


def test_ongoing_role_uses_current_year() -> None:
    assert total_years_experience([_wp(2018, None)], NOW) == 8.0  # 2018 -> 2026


def test_overlapping_roles_counted_once() -> None:
    # full-time 2016-2024 (8y) + concurrent consulting 2019-2022 (3y) -> span 8, NOT 11
    assert total_years_experience([_wp(2016, 2024), _wp(2019, 2022)], NOW) == 8.0


def test_fully_nested_overlap() -> None:
    assert total_years_experience([_wp(2010, 2020), _wp(2012, 2014)], NOW) == 10.0


def test_employment_gap_not_counted() -> None:
    # 2008-2012 (4y), gap, 2018-2024 (6y) -> 10, NOT 16 (the 6-year gap is excluded)
    assert total_years_experience([_wp(2008, 2012), _wp(2018, 2024)], NOW) == 10.0


def test_adjacent_touching_intervals_merge() -> None:
    assert total_years_experience([_wp(2010, 2015), _wp(2015, 2020)], NOW) == 10.0


def test_partial_overlap_union() -> None:
    # 2010-2016 and 2014-2020 overlap on 2014-2016 -> union 2010-2020 = 10
    assert total_years_experience([_wp(2010, 2016), _wp(2014, 2020)], NOW) == 10.0


def test_fractional_years() -> None:
    # Jan 2020 -> Jul 2020 = 0.5y
    assert total_years_experience([_wp(2020.0, 2020.5)], NOW) == 0.5


def test_unordered_input() -> None:
    assert total_years_experience([_wp(2018, 2024), _wp(2008, 2012)], NOW) == 10.0


def test_malformed_end_before_start_skipped() -> None:
    # bad period (end before start) is skipped, not negative
    assert total_years_experience([_wp(2020, 2010), _wp(2015, 2018)], NOW) == 3.0
