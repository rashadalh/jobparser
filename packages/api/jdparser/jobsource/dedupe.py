"""Search-result dedupe — SPEC §4.5. Pure; raises nothing.

Key = ``(final_url or redirect_url, company, title, location)`` with each component
stripped + lowercased. First occurrence wins; input order is preserved. At dedupe time
``final_url`` is usually absent (URL resolution happens later in ``extract/resolve.py``),
so the key falls back to ``redirect_url`` — adequate for first-pass dedupe.

Takes ``Job``, not raw provider JSON: the null-handling this module used to do by hand
now happens once, in the source client (see ``jdparser/jobs.py``).
"""

from jdparser.jobs import Job


def _key(job: Job) -> tuple[str, str, str, str]:
    final = (job.final_url or job.redirect_url).strip().lower()
    return (
        final,
        job.company.strip().lower(),
        job.title.strip().lower(),
        job.location.strip().lower(),
    )


def dedupe(jobs: list[Job]) -> list[Job]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Job] = []
    for j in jobs:
        k = _key(j)
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out
