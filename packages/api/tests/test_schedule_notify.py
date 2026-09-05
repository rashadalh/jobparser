"""Telegram recs notify — SPEC §3.4, §4.3–§4.5, §5.1–§5.2; IMPLEMENTATION_NOTIFY."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from jdparser.config import JDParserError, TELEGRAM_CHUNK_CHARS, TELEGRAM_SEND_TIMEOUT_S
from jdparser.schedule import notify as notify_mod
from jdparser.schedule.notify import (
    chunk_jobs,
    format_header,
    format_job,
    format_zero,
    is_new,
    load_telegram_credentials,
    notify_new_jobs,
    send_message,
)
from jdparser.schedule.schemas import NotifiedJob, NotifiedSet


def _ns(
    *,
    jobs: dict[str, NotifiedJob] | None = None,
    urls: dict[str, str] | None = None,
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> NotifiedSet:
    return NotifiedSet(jobs=jobs or {}, urls=urls or {}, updated_at=updated_at)


def _notified(
    job_id: str,
    final_url: str | None,
    *,
    title: str = "T",
    company: str = "C",
) -> NotifiedJob:
    return NotifiedJob(
        job_id=job_id,
        final_url=final_url,
        title=title,
        company=company,
        first_notified_at="2026-01-01T00:00:00+00:00",
        schedule_date="2026-01-01",
    )


def _job(
    *,
    job_id: str = "j1",
    title: str = "Engineer",
    company: str = "Acme",
    location: str = "Austin",
    final_url: str | None = "https://example.com/j1",
    quote: str = "built the matcher",
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "final_url": final_url,
        "judgment": {
            "met_requirements": [
                {"requirement": "Python", "evidence_quote": quote},
            ]
        },
    }


# --- is_new ------------------------------------------------------------------
def test_is_new_false_on_job_id_collision() -> None:
    """ID collision even if URL changed → not new (SPEC §3.4)."""
    ns = _ns(
        jobs={"j1": _notified("j1", "https://old.example/j1")},
        urls={"https://old.example/j1": "j1"},
    )
    job = _job(job_id="j1", final_url="https://new.example/j1")
    assert is_new(job, ns) is False


def test_is_new_false_on_final_url_collision() -> None:
    """New job_id, existing final_url → not new (SPEC §3.4)."""
    ns = _ns(
        jobs={"old": _notified("old", "https://example.com/same")},
        urls={"https://example.com/same": "old"},
    )
    job = _job(job_id="fresh", final_url="https://example.com/same")
    assert is_new(job, ns) is False


def test_is_new_true_for_unseen_id_and_url() -> None:
    ns = _ns(jobs={"other": _notified("other", "https://example.com/other")})
    ns.urls["https://example.com/other"] = "other"
    assert is_new(_job(job_id="j1", final_url="https://example.com/j1"), ns) is True


def test_is_new_ignores_empty_final_url() -> None:
    """Empty/None url is not a URL collision; only nonempty final_url is checked."""
    ns = _ns(urls={"https://example.com/x": "x"}, jobs={"x": _notified("x", "https://example.com/x")})
    assert is_new(_job(job_id="fresh", final_url=None), ns) is True
    assert is_new(_job(job_id="fresh2", final_url=""), ns) is True


# --- format_* ----------------------------------------------------------------
def test_format_header_exact_string() -> None:
    assert (
        format_header("2026-08-24", 3, 7)
        == "jdparser 2026-08-24: 3 new / 7 qualified"
    )


def test_format_zero_no_qualified() -> None:
    assert format_zero("2026-08-24", 0) == "jdparser 2026-08-24: no qualified jobs"


def test_format_zero_all_previously_sent() -> None:
    assert format_zero("2026-08-24", 4) == (
        "jdparser 2026-08-24: no new qualified jobs (4 qualified, all previously sent)"
    )


def test_format_job_block_and_missing_fields() -> None:
    text = format_job(_job())
    assert text == (
        "Engineer @ Acme\n"
        "Austin\n"
        "https://example.com/j1\n"
        "built the matcher"
    )
    empty = format_job(
        {
            "job_id": "z",
            "title": "",
            "company": "",
            "location": "",
            "final_url": None,
            "judgment": {"met_requirements": []},
        }
    )
    assert empty == "? @ ?\n?\n(no url)\n(no evidence quote)"


# --- chunk_jobs --------------------------------------------------------------
def test_chunk_jobs_packs_two_small_jobs() -> None:
    chunks = chunk_jobs("H", ["A", "B"], limit=20)
    assert chunks == ["H\n\nA\n\nB"]


def test_chunk_jobs_splits_when_over_limit() -> None:
    chunks = chunk_jobs("H", ["AAAA", "BBBB"], limit=8)
    # H\n\nAAAA = 7 <= 8; adding BBBB would be 13. Remaining chunk has no header.
    assert chunks == ["H\n\nAAAA", "BBBB"]


def test_chunk_jobs_truncates_giant_job_to_limit_ending_in_ellipsis() -> None:
    giant = "G" * 100
    chunks = chunk_jobs("H", [giant], limit=20)
    job_chunk = chunks[-1]
    assert len(job_chunk) == 20
    assert job_chunk.endswith("...")
    assert job_chunk == giant[:17] + "..."


# --- notify_new_jobs ---------------------------------------------------------
def test_notify_new_jobs_zero_does_not_put() -> None:
    sent: list[str] = []
    puts: list[NotifiedSet] = []
    ns = _ns()
    out, n = notify_new_jobs(
        schedule_date="2026-08-24",
        qualified_jobs=[],
        ns=ns,
        send=sent.append,
        put_notified=puts.append,
        now_iso="2026-08-24T12:00:00+00:00",
    )
    assert n == 0
    assert out is ns
    assert sent == [format_zero("2026-08-24", 0)]
    assert puts == []


def test_notify_new_jobs_previously_sent_does_not_put() -> None:
    sent: list[str] = []
    puts: list[NotifiedSet] = []
    job = _job(job_id="j1", final_url="https://example.com/j1")
    ns = _ns(
        jobs={"j1": _notified("j1", "https://example.com/j1")},
        urls={"https://example.com/j1": "j1"},
    )
    _, n = notify_new_jobs(
        schedule_date="2026-08-24",
        qualified_jobs=[job],
        ns=ns,
        send=sent.append,
        put_notified=puts.append,
        now_iso="2026-08-24T12:00:00+00:00",
    )
    assert n == 0
    assert sent == [format_zero("2026-08-24", 1)]
    assert puts == []


def test_notify_new_jobs_packed_chunk_puts_once_and_records_texts() -> None:
    sent: list[str] = []
    puts: list[NotifiedSet] = []
    j1 = _job(job_id="a", final_url="https://example.com/a", quote="q1")
    j2 = _job(job_id="b", title="Other", final_url="https://example.com/b", quote="q2")
    ns = _ns()
    out, n = notify_new_jobs(
        schedule_date="2026-08-24",
        qualified_jobs=[j1, j2],
        ns=ns,
        send=sent.append,
        put_notified=puts.append,
        now_iso="2026-08-24T12:00:00+00:00",
    )
    assert n == 2
    header = format_header("2026-08-24", 2, 2)
    assert sent == [chunk_jobs(header, [format_job(j1), format_job(j2)])[0]]
    assert len(puts) == 1  # one sent chunk that contained jobs
    assert set(out.jobs) == {"a", "b"}
    assert out.urls["https://example.com/a"] == "a"
    assert out.urls["https://example.com/b"] == "b"
    assert out.jobs["a"].first_notified_at == "2026-08-24T12:00:00+00:00"
    assert out.jobs["a"].schedule_date == "2026-08-24"
    assert out.updated_at == "2026-08-24T12:00:00+00:00"


def test_notify_new_jobs_mid_batch_failure_keeps_chunk1_jobs() -> None:
    """SPEC §3.4 add-on-success: send raising on chunk 2 leaves only chunk-1 jobs in ns."""
    j1 = _job(job_id="a", final_url="https://example.com/a", quote="A" * 2000)
    j2 = _job(job_id="b", final_url="https://example.com/b", quote="B" * 2000)
    header = format_header("2026-08-24", 2, 2)
    texts = chunk_jobs(header, [format_job(j1), format_job(j2)])
    assert len(texts) >= 2, "jobs must not fit in one TELEGRAM_CHUNK_CHARS chunk"

    sent: list[str] = []
    puts: list[NotifiedSet] = []

    def send(text: str) -> None:
        sent.append(text)
        if len(sent) >= 2:
            raise JDParserError(code="SCHEDULE_TELEGRAM_FAILED", message="chunk 2")

    ns = _ns()
    with pytest.raises(JDParserError) as ei:
        notify_new_jobs(
            schedule_date="2026-08-24",
            qualified_jobs=[j1, j2],
            ns=ns,
            send=send,
            put_notified=puts.append,
            now_iso="2026-08-24T12:00:00+00:00",
        )
    assert ei.value.code == "SCHEDULE_TELEGRAM_FAILED"
    assert len(sent) == 2
    assert len(puts) == 1  # only the successful job-containing chunk
    assert set(ns.jobs) == {"a"}
    assert "b" not in ns.jobs
    assert ns.urls == {"https://example.com/a": "a"}


# --- send_message -------------------------------------------------------------
class _FakeResp:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_send_message_posts_json_via_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req: object, timeout: object = None) -> _FakeResp:
        captured["url"] = getattr(req, "full_url", None)
        captured["data"] = getattr(req, "data", None)
        captured["timeout"] = timeout
        captured["method"] = req.get_method() if hasattr(req, "get_method") else None
        return _FakeResp(200)

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)
    send_message("hello", token="tok", chat_id="42")
    assert captured["url"] == "https://api.telegram.org/bottok/sendMessage"
    assert captured["timeout"] == 10
    assert captured["method"] == "POST"
    body = json.loads(captured["data"])
    assert body == {
        "chat_id": "42",
        "text": "hello",
        "disable_web_page_preview": True,
    }
    assert "parse_mode" not in body


def test_send_message_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: object = None) -> _FakeResp:
        return _FakeResp(400)

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(JDParserError) as ei:
        send_message("hello", token="tok", chat_id="42")
    assert ei.value.code == "SCHEDULE_TELEGRAM_FAILED"


def test_send_message_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: object = None) -> _FakeResp:
        raise HTTPError(
            "https://api.telegram.org/botxxx/sendMessage",
            401,
            "Unauthorized",
            hdrs=None,  # type: ignore[arg-type]  # reason: urllib HTTPError accepts None headers in tests
            fp=None,
        )

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(JDParserError) as ei:
        send_message("hello", token="tok", chat_id="42")
    assert ei.value.code == "SCHEDULE_TELEGRAM_FAILED"


def test_send_message_transport_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: object = None) -> _FakeResp:
        raise URLError("timed out")

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(JDParserError) as ei:
        send_message("hello", token="tok", chat_id="42")
    assert ei.value.code == "SCHEDULE_TELEGRAM_FAILED"


# --- load_telegram_credentials ------------------------------------------------
def test_load_telegram_credentials_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_mod, "_cached_telegram_creds", None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
    token, chat_id = load_telegram_credentials(secret_arn="arn:should-not-hit-boto3")
    assert (token, chat_id) == ("env-token", "env-chat")


def test_load_telegram_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_mod, "_cached_telegram_creds", None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_SECRET_ARN", raising=False)
    with pytest.raises(JDParserError) as ei:
        load_telegram_credentials()
    assert ei.value.code == "SCHEDULE_SECRET_MISSING"


def test_chunk_jobs_default_limit_matches_config() -> None:
    """Sanity: production default is TELEGRAM_CHUNK_CHARS (used by notify_new_jobs)."""
    assert TELEGRAM_CHUNK_CHARS == 3500


def test_telegram_relay_chunk_and_timeout_match_config() -> None:
    """Alarm zip cannot import jdparser; names/values must match config.py."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "lambda"
        / "telegram_relay"
        / "handler.py"
    ).read_text()
    assert f"TELEGRAM_CHUNK_CHARS = {TELEGRAM_CHUNK_CHARS}" in text
    assert f"TELEGRAM_SEND_TIMEOUT_S = {TELEGRAM_SEND_TIMEOUT_S}" in text
