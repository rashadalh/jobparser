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
SEARCH_PLAN_MAX_QUERIES: int = 6            # count; cap on planner output queries
ADZUNA_MAX_PAGES: int = 3                   # count; max pages per query (path param)
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
ADZUNA_COUNTRY: str = "us"                  # Adzuna country code (MVP-fixed, §8/§9)
ADZUNA_BASE_URL: str = "https://api.adzuna.com/v1/api"
EVAL_FANOUT_CONCURRENCY: int = 8            # count; max concurrent job-eval workers
FETCH_TIMEOUT_S: int = 20                   # seconds; httpx request timeout
PLAYWRIGHT_TIMEOUT_MS: int = 30000          # milliseconds; Playwright nav/render timeout
HTTP_MAX_RETRIES: int = 2                   # count; httpx retry attempts on 5xx/timeout
PARSER_VERSION: str = "1.0.0"               # semver; resume text-extraction logic version
SCHEMA_VERSION: str = "1.1.0"               # semver; ResumeProfile schema version (1.1.0: added `education`)

# --- §6.3 LLM constants & model routing --------------------------------------
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
MODEL_GLM: str = "z-ai/glm-5.2"                          # logic
MODEL_GEMINI_FLASH_LITE: str = "google/gemini-3.1-flash-lite"  # text extraction
LLM_MAX_RETRIES: int = 2                     # instructor re-ask count on validation failure

# --- Durable flat-file stores (SPEC §3.7/§3.9; created at import) ------------
DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
PROFILES_DIR: Path = DATA_DIR / "profiles"   # resume cache:  {cache_key}.json
RUNS_DIR: Path = DATA_DIR / "runs"           # run records:   {run_id}.json
UPLOADS_DIR: Path = DATA_DIR / "uploads"     # saved uploads: {run_id}.{ext}
for _d in (PROFILES_DIR, RUNS_DIR, UPLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

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
    "profiler":  _node_cfg("PROFILER",  MODEL_GLM,               "0.2", "8000",  "off"),
    "planner":   _node_cfg("PLANNER",   MODEL_GLM,               "0.3", "4000",  "off"),
    "jd_parser": _node_cfg("JD_PARSER", MODEL_GEMINI_FLASH_LITE, "0.1", "6000",  "off"),
    "judge":     _node_cfg("JUDGE",     MODEL_GLM,               "0.2", "10000", "low"),
}
