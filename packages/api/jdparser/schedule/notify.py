"""Telegram recs formatting, send, and notified-set merge.

SPEC §3.4, §4.3–§4.5, §5.1–§5.2, §6; IMPLEMENTATION_NOTIFY.md.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from jdparser.config import (
    JDParserError,
    TELEGRAM_CHUNK_CHARS,
    TELEGRAM_SEND_TIMEOUT_S,
)
from jdparser.schedule.schemas import NotifiedJob, NotifiedSet

# Process-lifetime cache for Secrets Manager (env vars still win on every call).
_cached_telegram_creds: tuple[str, str] | None = None


def _nonempty_url(job: dict[str, Any]) -> str | None:
    url = job.get("final_url")
    if isinstance(url, str) and url:
        return url
    return None


def _display(job: dict[str, Any], key: str) -> str:
    value = job.get(key)
    if isinstance(value, str) and value:
        return value
    return "?"


def _job_id(job: dict[str, Any]) -> str:
    value = job.get("job_id")
    if isinstance(value, str):
        return value
    return str(value) if value is not None else ""


def is_new(job: dict[str, Any], ns: NotifiedSet) -> bool:
    """False if job_id in ns.jobs OR (final_url is a non-empty string AND final_url in ns.urls)."""
    if _job_id(job) in ns.jobs:
        return False
    url = _nonempty_url(job)
    if url is not None and url in ns.urls:
        return False
    return True


def format_header(schedule_date: str, new_count: int, qualified_count: int) -> str:
    """Exactly: jdparser {schedule_date}: {new_count} new / {qualified_count} qualified"""
    return f"jdparser {schedule_date}: {new_count} new / {qualified_count} qualified"


def format_zero(schedule_date: str, qualified_count: int) -> str:
    """SPEC §5.2 heartbeat when there are no new qualified jobs to send."""
    if qualified_count == 0:
        return f"jdparser {schedule_date}: no qualified jobs"
    return (
        f"jdparser {schedule_date}: no new qualified jobs "
        f"({qualified_count} qualified, all previously sent)"
    )


def format_job(job: dict[str, Any]) -> str:
    """SPEC §5.1 job block (title, company, location, url, first evidence quote)."""
    raw_url = job.get("final_url")
    url = raw_url if isinstance(raw_url, str) and raw_url else "(no url)"
    met = (job.get("judgment") or {}).get("met_requirements") or []
    quote = (met[0].get("evidence_quote") if met else None) or "(no evidence quote)"
    return (
        f"{job.get('title') or '?'} @ {job.get('company') or '?'}\n"
        f"{job.get('location') or '?'}\n"
        f"{url}\n"
        f"{quote}"
    )


def _truncate_job(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return ("..." if limit == 3 else text[:limit])[:limit]
    return text[: limit - 3] + "..."


def _chunk_jobs_with_indices(
    header: str, job_texts: list[str], limit: int
) -> list[tuple[str, list[int]]]:
    """Pack header + jobs into chunks; each tuple is (text, indices into job_texts)."""
    packed: list[tuple[str, list[int]]] = []

    first_parts: list[str] = [header]
    first_idxs: list[int] = []
    first_len = len(header)
    i = 0
    while i < len(job_texts):
        extra = 2 + len(job_texts[i])
        if first_len + extra <= limit:
            first_parts.append(job_texts[i])
            first_idxs.append(i)
            first_len += extra
            i += 1
        else:
            break
    packed.append(("\n\n".join(first_parts), first_idxs))

    current_parts: list[str] = []
    current_idxs: list[int] = []
    current_len = 0
    while i < len(job_texts):
        job = _truncate_job(job_texts[i], limit)
        extra = (2 + len(job)) if current_parts else len(job)
        if current_parts and current_len + extra > limit:
            packed.append(("\n\n".join(current_parts), current_idxs))
            current_parts = []
            current_idxs = []
            current_len = 0
            continue
        if not current_parts:
            current_parts = [job]
            current_idxs = [i]
            current_len = len(job)
        else:
            current_parts.append(job)
            current_idxs.append(i)
            current_len += extra
        i += 1
    if current_parts:
        packed.append(("\n\n".join(current_parts), current_idxs))
    return packed


def chunk_jobs(header: str, job_texts: list[str], limit: int = TELEGRAM_CHUNK_CHARS) -> list[str]:
    """First chunk is header + as many jobs as fit (blank line between).

    Remaining jobs get their own chunks, packed to `limit`.
    A single job longer than `limit` is truncated to `limit` (last 3 chars `...`).
    """
    return [text for text, _ in _chunk_jobs_with_indices(header, job_texts, limit)]


def send_message(
    text: str,
    *,
    token: str,
    chat_id: str,
    timeout_s: int = TELEGRAM_SEND_TIMEOUT_S,
) -> None:
    """POST https://api.telegram.org/bot{token}/sendMessage

    JSON body: chat_id, text, disable_web_page_preview: true. No parse_mode.
    Non-2xx or transport error → JDParserError(SCHEDULE_TELEGRAM_FAILED).
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = int(getattr(resp, "status", 200))
            if not (200 <= status < 300):
                raise JDParserError(
                    code="SCHEDULE_TELEGRAM_FAILED",
                    message=f"HTTP {status}",
                )
    except JDParserError:
        raise
    except urllib.error.HTTPError as exc:
        raise JDParserError(
            code="SCHEDULE_TELEGRAM_FAILED",
            message=f"HTTP {exc.code}",
        ) from exc
    except Exception as exc:
        raise JDParserError(
            code="SCHEDULE_TELEGRAM_FAILED",
            message="telegram send failed",
        ) from exc


def load_telegram_credentials(secret_arn: str | None = None) -> tuple[str, str]:
    """Env TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID win if both set.

    Else Secrets Manager: boto3 imported ONLY inside this function (lazy).
    JSON keys TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
    Missing → JDParserError(SCHEDULE_SECRET_MISSING).
    Cache the secret on the module (process-lifetime).
    """
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    env_chat = os.environ.get("TELEGRAM_CHAT_ID") or ""
    if env_token and env_chat:
        return env_token, env_chat

    global _cached_telegram_creds
    if _cached_telegram_creds is not None:
        return _cached_telegram_creds

    arn = secret_arn or os.environ.get("TELEGRAM_SECRET_ARN") or ""
    if not arn:
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message="TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset and no secret ARN",
        )

    import boto3  # lazy: unit tests of format/chunk/notify_new_jobs need no AWS

    from jdparser.schedule.secrets import load_secret_object

    parsed = load_secret_object(
        boto3.client("secretsmanager"), arn, label="telegram"
    )
    token_val = parsed.get("TELEGRAM_BOT_TOKEN")
    chat_val = parsed.get("TELEGRAM_CHAT_ID")
    token = token_val if isinstance(token_val, str) else ""
    chat_id = chat_val if isinstance(chat_val, str) else ""
    if not token or not chat_id:
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message="telegram secret missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
        )
    _cached_telegram_creds = (token, chat_id)
    return _cached_telegram_creds


def _add_notified(
    ns: NotifiedSet,
    job: dict[str, Any],
    *,
    schedule_date: str,
    now_iso: str,
) -> None:
    job_id = _job_id(job)
    url = _nonempty_url(job)
    ns.jobs[job_id] = NotifiedJob(
        job_id=job_id,
        final_url=url,
        title=_display(job, "title"),
        company=_display(job, "company"),
        first_notified_at=now_iso,
        schedule_date=schedule_date,
    )
    if url is not None:
        ns.urls[url] = job_id
    ns.updated_at = now_iso


def notify_new_jobs(
    *,
    schedule_date: str,
    qualified_jobs: list[dict[str, Any]],
    ns: NotifiedSet,
    send: Callable[[str], None],
    put_notified: Callable[[NotifiedSet], None],
    now_iso: str,
) -> tuple[NotifiedSet, int]:
    """Filter is_new; send zero-message or header+chunks; merge NotifiedSet per successful job.

    Returns (updated_ns, new_notified_count).
    """
    new_jobs = [j for j in qualified_jobs if is_new(j, ns)]
    if not new_jobs:
        send(format_zero(schedule_date, len(qualified_jobs)))
        return ns, 0

    packed = _chunk_jobs_with_indices(
        format_header(schedule_date, len(new_jobs), len(qualified_jobs)),
        [format_job(j) for j in new_jobs],
        TELEGRAM_CHUNK_CHARS,
    )
    for text, idxs in packed:
        send(text)
        if idxs:
            for i in idxs:
                _add_notified(
                    ns, new_jobs[i], schedule_date=schedule_date, now_iso=now_iso
                )
            ns.updated_at = now_iso
            put_notified(ns)
    return ns, len(new_jobs)
