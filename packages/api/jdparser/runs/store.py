"""Durable run-record store — SPEC §3.10 (run-record lifecycle), §4.8, §5.2.

Per-run JSON flat-files at ``data/runs/{run_id}.json`` (SPEC §9: MVP-stubbed; eventual
form is DB-backed). ``create_run`` seeds a ``pending`` record at ``POST /api/runs``;
the background task drives it to ``running`` then a terminal ``completed``/``failed``
via ``update_run`` (atomic merge-write). Records are never auto-deleted (§3.10).

Each write is atomic (temp file in ``RUNS_DIR`` + ``os.replace``) — the same durability
pattern as ``cache/store.put_profile`` — so a polling reader never sees a partial record.
"""

import os
import tempfile
from typing import Any

from jdparser.config import RUNS_DIR, now_iso
from jdparser.llm.schemas import RunRecord


def _write(record: RunRecord) -> None:
    """Atomically persist ``record`` to ``RUNS_DIR/{run_id}.json`` (temp + ``os.replace``)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.run_id}.json"
    fd, tmp = tempfile.mkstemp(dir=RUNS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(record.model_dump_json(indent=2))
        os.replace(tmp, path)
    except BaseException:
        # Don't leave a stray temp file behind on a failed write.
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def create_run(run_id: str, user_id: str, resume_file_path: str) -> RunRecord:
    """Seed a ``pending`` run record (SPEC §4.8, §3.10 run-record lifecycle).

    ``resume_file_path`` is accepted for signature parity (SPEC §4.8) but is not a
    ``RunRecord`` field — the server derives the upload path from ``run_id``.
    """
    rec = RunRecord(
        run_id=run_id,
        user_id=user_id,
        status="pending",
        created_at=now_iso(),
        updated_at=now_iso(),
        resume_cache_hit=None,
        qualified_jobs=[],
        failures=[],
        rejected=[],
        errors=[],
        error=None,
    )
    _write(rec)
    return rec


def get_run(run_id: str) -> RunRecord | None:
    """Return the run record for ``run_id``, or ``None`` if no file exists."""
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs() -> list[RunRecord]:
    """All run records, newest-created first (run history). Stale/partial files are
    skipped rather than failing the whole listing."""
    out: list[RunRecord] = []
    for path in RUNS_DIR.glob("*.json"):
        try:
            out.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:  # reason: skip a stale/partial record, don't fail the listing
            continue
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


# reason: heterogeneous RunRecord field updates (SPEC §5.2)
def update_run(run_id: str, **fields: Any) -> RunRecord:
    """Atomic merge-write: load existing, overlay ``fields`` + a fresh ``updated_at``,
    re-validate, and persist (SPEC §4.8). The record must already exist (``create_run``
    seeded it at ``POST /api/runs``)."""
    rec = get_run(run_id)
    assert rec is not None, f"update_run: no record for run_id {run_id!r}"  # create_run ran first
    data = rec.model_dump() | fields | {"updated_at": now_iso()}
    updated = RunRecord.model_validate(data)
    _write(updated)
    return updated
