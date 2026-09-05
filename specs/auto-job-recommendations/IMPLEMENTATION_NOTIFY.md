# IMPLEMENTATION_NOTIFY — Telegram recs

> Owns recs formatting, Bot API send, and per-job notified-set updates.
> References: SPEC §3.4, §4.3–§4.5, §5.1–§5.2, §6.

## Purpose

Send only **new** qualified jobs to a single allowlisted chat. Persist each
successful send immediately so a later chunk failure does not re-send earlier
jobs.

## Files this area owns

- `packages/api/jdparser/schedule/notify.py`
- `packages/api/tests/test_schedule_notify.py`

Must NOT edit `store.py` beyond importing `NotifiedSet` / `NotifiedJob`;
must NOT edit `infra/` or `graph/`.

## `notify.py`

```python
def is_new(job: dict[str, Any], ns: NotifiedSet) -> bool:
    """False if job_id in ns.jobs or (final_url nonempty and in ns.urls)."""

def format_header(schedule_date: str, new_count: int, qualified_count: int) -> str:
    """SPEC §5.1 header line exactly."""

def format_zero(schedule_date: str, qualified_count: int) -> str:
    """SPEC §5.2 exactly (both qualified_count==0 and all-previously-sent)."""

def format_job(job: dict[str, Any]) -> str:
    """SPEC §5.1 job block. evidence = first met_requirements item evidence_quote."""

def chunk_jobs(header: str, job_texts: list[str], limit: int = TELEGRAM_CHUNK_CHARS) -> list[str]:
    """First chunk is header + as many jobs as fit (blank line between).
    Remaining jobs get their own chunks, packed to `limit`. A single job longer
    than `limit` is truncated to `limit` (last 3 chars `...`)."""

def send_message(text: str, *, token: str, chat_id: str, timeout_s: int = TELEGRAM_SEND_TIMEOUT_S) -> None:
    """POST sendMessage. Non-2xx → JDParserError(SCHEDULE_TELEGRAM_FAILED).
    stdlib urllib.request only (match infra/lambda/telegram_relay/handler.py)."""

def notify_new_jobs(
    *,
    schedule_date: str,
    qualified_jobs: list[dict[str, Any]],
    ns: NotifiedSet,
    send: Callable[[str], None],
    put_notified: Callable[[NotifiedSet], None],
    now_iso: str,
) -> tuple[NotifiedSet, int]:
    """Filter is_new; send zero-message or header+chunks; merge NotifiedSet per
    successful job. Returns (updated_ns, new_notified_count)."""
```

### `notify_new_jobs` order (normative)

1. `new_jobs = [j for j in qualified_jobs if is_new(j, ns)]`
2. If `new_jobs` is empty: `send(format_zero(...))`; return `(ns, 0)` — do
   **not** rewrite notified.json.
3. `send` each string from `chunk_jobs(format_header(...), [format_job(j) for j in new_jobs])`.
4. After **each** job's text has been included in a **successfully sent**
   chunk, add that job to `ns` and `put_notified(ns)`.
   Packing: if a chunk contains jobs 0..k, mark 0..k notified only after that
   chunk's `send` returns. Jobs in unsent later chunks stay un-notified.
5. Return `(ns, len(new_jobs))` if all chunks sent. If `send` raises mid-batch,
   propagate; `ns` already contains the prefix that was sent.

### Evidence line

```python
met = (job.get("judgment") or {}).get("met_requirements") or []
quote = (met[0].get("evidence_quote") if met else None) or "(no evidence quote)"
```

Title/company/location/url: `job.get("title") or "?"` (same for company,
location). `final_url` missing → the literal `"(no url)"`.

### Credentials helper (production)

```python
def load_telegram_credentials(secret_arn: str | None = None) -> tuple[str, str]:
    """Env TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID win; else Secrets Manager JSON.
    Missing → JDParserError(SCHEDULE_SECRET_MISSING). Cache the secret on the
    module (process-lifetime) like black-scholes-binary telegram_relay."""
```

`notify.py` may import `boto3` only inside `load_telegram_credentials` so unit
tests of format/chunk/`notify_new_jobs` need no AWS.

## Tests

- `is_new` by `job_id` and by `final_url`.
- `format_zero` both branches (0 qualified vs all previously sent).
- `chunk_jobs` packs two small jobs in one chunk; splits when over limit;
  truncates a giant job.
- `notify_new_jobs`: fake `send` records texts; `put_notified` call count
  equals number of sent chunks that contained jobs; a raising `send` on chunk 2
  leaves only chunk-1 jobs in `ns`.
- `send_message` against a monkeypatched `urllib.request.urlopen`.
  Do not use `respx` (httpx). Do not hit the live Bot API.

## Done when

`uv run pytest tests/test_schedule_notify.py` green; mypy clean on `notify.py`.
