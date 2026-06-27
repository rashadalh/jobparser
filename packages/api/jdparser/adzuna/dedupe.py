"""Adzuna result dedupe — SPEC §4.5. Pure; raises nothing.

Key = ``(final_url or redirect_url, company.display_name, title,
location.display_name)`` with each component stripped + lowercased. First
occurrence wins; input order is preserved. At dedupe time ``final_url`` is
usually absent (URL resolution happens later in ``extract/resolve.py``), so the
key falls back to ``redirect_url`` — adequate for first-pass dedupe.

Raw Adzuna job dicts are heterogeneous JSON, hence ``dict[str, Any]``.
"""

from typing import Any


# reason: heterogeneous Adzuna JSON passthrough (SPEC §3.8.1)
def _key(job: dict[str, Any]) -> tuple[str, str, str, str]:
    final = (job.get("final_url") or job.get("redirect_url") or "").strip().lower()
    company = (job.get("company", {}).get("display_name") or "").strip().lower()
    title = (job.get("title") or "").strip().lower()
    loc = (job.get("location", {}).get("display_name") or "").strip().lower()
    return (final, company, title, loc)


# reason: heterogeneous Adzuna JSON passthrough (SPEC §3.8.1)
def dedupe(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    # reason: heterogeneous Adzuna JSON passthrough (SPEC §3.8.1)
    out: list[dict[str, Any]] = []
    for j in jobs:
        k = _key(j)
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out
