"""Settings, environment, and constants — SPEC §6 single source of truth.

Every literal whose meaning depends on context is annotated with a unit. The
canonical ``LLM_NODES`` mapping (SPEC §6.3) is env-overridable per node.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

# Load packages/api/.env regardless of CWD (this file is jdparser/config.py).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# --- Typed application error (IMPLEMENTATION.md §conventions) -----------------
class JDParserError(Exception):
    """Single flat typed error. Instantiate with a ``code=`` from SPEC §6.4.

    Per-job stages catch this and convert to an ``ErrorRecord`` + a ``failed``
    ``EvaluatedJob``; they never crash the run. Do not create per-stage
    subclasses.
    """

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def now_iso() -> str:
    """ISO-8601 UTC timestamp (IMPLEMENTATION.md §conventions — Time)."""
    return datetime.now(timezone.utc).isoformat()


# --- §6.1 Pipeline constants -------------------------------------------------
CONFIDENCE_THRESHOLD: float = 0.75          # fraction [0,1]; min FitJudgment.confidence to display
MIN_RESUME_CHARS: int = 200                 # characters; min normalized resume text
MAX_RESUME_CHARS: int = 40000               # characters; truncate resume before profiler LLM
MIN_JD_CHARS: int = 600                     # characters; min extracted JD length to pass quality
MAX_JD_CHARS: int = 60000                   # characters; truncate JD before LLM (cost guard)
JD_BOILERPLATE_MAX_RATIO: float = 0.40      # fraction; max nav/boilerplate share before quality fail
# words; a "line" longer than this is real prose that happens to mention a boilerplate
# phrase (e.g. a full JD ending in "...All rights reserved."), not a standalone nav/footer
# line — the substring check only applies at or under this length (SPEC §6.1/quality.py).
BOILERPLATE_LINE_MAX_WORDS: int = 12
# count; cap on planner output queries — env-overridable (bumped 6->8: more room for
# distinct role-variant coverage, esp. when there's no location signal to scope by).
SEARCH_PLAN_MAX_QUERIES: int = int(os.getenv("SEARCH_PLAN_MAX_QUERIES", "8"))
SEARCH_MAX_DAYS_OLD_DEFAULT: int = 7        # days; default listing-age filter (0 = any age)
# count; max pages per query (path param) — env-overridable (bumped 3->5: a nationwide/
# unscoped query spreads its fixed results_per_page budget over a much larger area than a
# geo-scoped one, so a niche/geographically-concentrated field can starve at pages=3).
ADZUNA_MAX_PAGES: int = int(os.getenv("ADZUNA_MAX_PAGES", "5"))
ADZUNA_DEFAULT_RESULTS_PER_PAGE: int = 20   # count; schema default for results_per_page
ADZUNA_MAX_RESULTS_PER_PAGE: int = 50       # count; Adzuna hard max (schema upper bound)
# A real browser UA. The original polite-bot string ("...compatible; jdparser/1.0...")
# is 403'd by Adzuna's landing pages (and many ATS bot-protections), which makes the
# core JD-extraction flow (SPEC §7.5) impossible. Empirically a browser UA returns the
# full JobPosting JSON-LD. Spec-corrected per SPEC's "update this doc" preamble (§6.1).
HTTP_USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Referer sent on EVERY page fetch and URL resolution (fetch.py / resolve.py).
#
# The value is deliberately provider-specific even though this is a provider-agnostic
# layer: Adzuna's own `/land/...` redirect pages 403 a referrer-less request even with a
# browser UA (observed: real browser traffic always carries a Referer, ours didn't), and
# the JD-extraction flow starts by following exactly those redirects. Chosen empirically,
# not by principle — do not "generalize" it to the target host or drop it. Cheap to set, no
# guarantee against an IP-reputation-based block specifically.
HTTP_REFERER: str = "https://www.adzuna.com/"
ADZUNA_COUNTRY: str = "us"                  # Adzuna country code (MVP-fixed, §8/§9)
ADZUNA_BASE_URL: str = "https://api.adzuna.com/v1/api"
EVAL_FANOUT_CONCURRENCY: int = 8            # count; max concurrent job-eval workers
# Bounded funnel: hard ceiling on how many screened jobs reach the expensive per-job
# fan-out, so a wide search pull can't blow past the frontend poll timeout. The screen
# RANKS by relevance and we keep the top N; the rest are recorded as screened_out
# (reason "over_cap"). Applied even when the screen errors/returns junk (timeout guard).
SCREEN_EVAL_CAP: int = int(os.getenv("SCREEN_EVAL_CAP", "80"))  # count; max jobs evaluated
# Max jobs per screener LLM call. The screener's output enumerates every relevant/agency
# id, so its token cost scales with pool size, not just a fixed prompt — a wide nationwide
# pull (1000+ deduped jobs) overflows a single call's max_tokens and truncates. screen_jobs
# chunks the deduped pool into batches of this size and merges the results instead.
SCREEN_BATCH_SIZE: int = int(os.getenv("SCREEN_BATCH_SIZE", "150"))
FETCH_TIMEOUT_S: int = 20                   # seconds; httpx request timeout
PLAYWRIGHT_TIMEOUT_MS: int = 30000          # milliseconds; Playwright nav/render timeout
# count; retry attempts for transient upstream failures. NOTE the two uses differ:
# httpx's `HTTPTransport(retries=)` retries CONNECTION errors only and does nothing for an
# HTTP error response (verified: retries=5 against a 503 issues exactly one request), so
# retrying a 5xx takes an explicit request loop — see jobsource/adzuna/client.search.
HTTP_MAX_RETRIES: int = 2
PARSER_VERSION: str = "1.2.0"               # semver; resume parsing logic version (1.1.0: total-career years; 1.2.0: per-bullet evidence, not theme summaries)
SCHEMA_VERSION: str = "1.2.0"               # semver; ResumeProfile schema (1.1.0: education; 1.2.0: work_periods)

# --- §6.3 LLM constants & model routing --------------------------------------
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
# The `~` prefix marks an OpenRouter floating pointer: it always redirects to the newest
# model in the DeepSeek V4 Flash family, so the model actually serving these nodes changes
# without a commit here. The per-node temps/max_tokens/reasoning below are calibrated
# against whatever it pointed at when they were set. Pin a dated slug
# (e.g. deepseek/deepseek-v4-flash-0731) if a run needs reproducibility.
MODEL_LOGIC: str = "~deepseek/deepseek-v4-flash-latest"     # logic (profiler / planner / judge)
# Was MODEL_GEMINI_FLASH_LITE; renamed because a constant naming its vendor lies the
# moment the routing changes. Same slug as MODEL_LOGIC today — the split is routing intent.
MODEL_EXTRACT: str = "~deepseek/deepseek-v4-flash-latest"   # text extraction (jd_parser / screener)
LLM_MAX_RETRIES: int = 2                     # instructor re-ask count on validation failure

# --- Durable flat-file stores (SPEC §3.7/§3.9; created at import) ------------
DATA_DIR: Path = Path(os.getenv("JDPARSER_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
PROFILES_DIR: Path = DATA_DIR / "profiles"   # resume cache:  {cache_key}.json
RUNS_DIR: Path = DATA_DIR / "runs"           # run records:   {run_id}.json
UPLOADS_DIR: Path = DATA_DIR / "uploads"     # saved uploads: {run_id}.{ext}
for _d in (PROFILES_DIR, RUNS_DIR, UPLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Daily schedule plane (specs/auto-job-recommendations/SPEC.md §6) --------
SCHEDULE_TZ: str = "America/Chicago"
SCHEDULE_LOCK_STALE_S: int = 900             # seconds; 15 min; equal to Lambda timeout
TELEGRAM_CHUNK_CHARS: int = 3500             # Python len(str); Bot API cap is 4096
TELEGRAM_SEND_TIMEOUT_S: int = 10            # seconds; urlopen timeout

# --- Secrets (from env; .env gitignored) -------------------------------------
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_APP_URL: str = os.getenv("OPENROUTER_APP_URL", "")
OPENROUTER_APP_TITLE: str = os.getenv("OPENROUTER_APP_TITLE", "jdparser")
ADZUNA_APP_ID: str = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY: str = os.getenv("ADZUNA_APP_KEY", "")


# --- §6.3 Per-node LLM config (env-overridable; generous defaults) -----------
class NodeCfg(TypedDict):
    model: str
    temperature: float
    max_tokens: int
    reasoning: str  # off|low|medium|high


def _node_cfg(node: str, model: str, temp: str, max_tokens: str, reasoning: str) -> NodeCfg:
    """Build one node config, each field falling back to the default when its
    ``LLM_{FIELD}_{NODE}`` env var is unset (SPEC §6.3)."""
    return {
        "model": os.getenv(f"LLM_MODEL_{node}", model),
        "temperature": float(os.getenv(f"LLM_TEMP_{node}", temp)),
        "max_tokens": int(os.getenv(f"LLM_MAX_TOKENS_{node}", max_tokens)),
        "reasoning": os.getenv(f"LLM_REASONING_{node}", reasoning),
    }


LLM_NODES: dict[str, NodeCfg] = {
    "profiler":  _node_cfg("PROFILER",  MODEL_LOGIC,               "0.2", "8000",  "off"),
    "planner":   _node_cfg("PLANNER",   MODEL_LOGIC,               "0.3", "4000",  "off"),
    "jd_parser": _node_cfg("JD_PARSER", MODEL_EXTRACT,             "0.1", "6000",  "off"),
    "judge":     _node_cfg("JUDGE",     MODEL_LOGIC,               "0.2", "10000", "medium"),
    # relevance pre-screen over Adzuna titles+snippets (cheap, batched): coarse same-field
    # filter before the expensive per-job evaluation. DeepSeek V4 Flash (latest); output is just ids.
    "screener":  _node_cfg("SCREENER",  MODEL_EXTRACT,             "0.1", "4000",  "off"),
    # merges free-text feedback into the candidate's existing note list (not blind-append).
    "feedback":  _node_cfg("FEEDBACK",  MODEL_LOGIC,               "0.2", "2000",  "off"),
}
