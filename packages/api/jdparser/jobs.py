"""The `Job` domain model — one normalized shape for a job posting.

Job postings enter the system as a provider's raw JSON. Before this model existed, every
consumer re-destructured that raw dict with its own null-handling — `dedupe`, the screener,
the screened-out recorder, and the subgraph's `_job_meta` each had their own copy. They
diverged: `adzuna/dedupe.py` used `.get("company", {})`, which handles an ABSENT key but not
a present-but-null one, and crashed the whole run on a listing Adzuna returned with
`"company": null` (see docs/REFACTOR_AUDIT.md F1/F2).

So: normalize ONCE, at the source boundary (`jobsource/*/client.py`), and let every consumer
downstream read typed fields. There is exactly one place left that touches provider JSON.

`raw` carries the untouched provider payload through to `EvaluatedJob.source`, which is
persisted in `data/runs/*.json` — it must survive the trip byte-identically.
"""

from typing import Any

from pydantic import BaseModel


class Job(BaseModel):
    """A job posting, normalized at the source boundary."""

    id: str
    title: str
    company: str
    location: str
    description: str          # the short search-result snippet, NOT the full JD
    redirect_url: str
    # Provider payload, untouched. Flows to EvaluatedJob.source and is persisted in the
    # run record — never rebuild this from the fields above.
    raw: dict[str, Any]
    # A resolved employer URL, if the provider happened to supply one. Adzuna never does
    # (resolution happens later, in extract/resolve.py); kept because `dedupe` prefers it
    # over redirect_url when present, and that precedence is covered by tests.
    final_url: str | None = None
    # Set at the relevance screen, not by the source. Rides through to the UI "Agency" badge.
    is_recruitment_agency: bool = False
