"""Deterministic years-of-experience computation from dated work periods.

The profiler LLM extracts `work_periods` (each role's start/end as decimal years); this
module computes `total_years_experience` from them in pure Python — no LLM arithmetic.

Algorithm: **interval union**. Treat each role as a `[start, end]` interval, merge any
that overlap, and sum the merged lengths. This is correct for BOTH:
  - overlapping/concurrent roles  -> counted once (no double-count), and
  - employment gaps               -> not counted (a break is not experience).
An ongoing role (`end_year is None`) ends at `current_year`.
"""

from datetime import datetime, timezone

from jdparser.llm.schemas import WorkPeriod


def current_decimal_year() -> float:
    """The present moment as a decimal year (e.g. 2026.49), for ongoing roles."""
    now = datetime.now(timezone.utc)
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    next_year = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return now.year + (now - year_start).total_seconds() / (next_year - year_start).total_seconds()


def total_years_experience(periods: list[WorkPeriod], current_year: float) -> float:
    """Total career length via interval union of dated `periods`, rounded to 0.1 year.

    Malformed periods (end before start) are skipped. Returns 0.0 for no periods.
    """
    spans: list[tuple[float, float]] = []
    for p in periods:
        end = current_year if p.end_year is None else p.end_year
        if end >= p.start_year:  # skip malformed (end before start)
            spans.append((p.start_year, end))
    spans.sort()

    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:           # overlaps the current merged span
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:                                            # disjoint (a gap) -> new span
            merged.append((start, end))

    return round(sum(end - start for start, end in merged), 1)
