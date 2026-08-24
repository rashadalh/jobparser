# IMPLEMENTATION_LLM — the six LLM agents (OpenRouter)

> Owns all Pydantic schemas and the four LLM nodes. References: SPEC §3.3–§3.8.4,
> §4.4, §6.3, §6.4. **Provider is OpenRouter** (OpenAI-compatible) accessed via the
> **OpenAI Python SDK** (`openai`) + **`instructor`** for Pydantic-validated
> structured output. Models per SPEC §6.3 routing table: **DeepSeek V4 Flash (latest)** (`~deepseek/deepseek-v4-flash-latest`)
> for logic, **DeepSeek V4 Flash (latest)** (`~deepseek/deepseek-v4-flash-latest`) for text
> extraction.

## Purpose

Each LLM has one job, one schema, one failure mode (per the overview). Outputs are
strict JSON validated against Pydantic models by `instructor`, which re-asks the
model on a validation failure before raising. Logic work → DeepSeek V4 Flash (latest); literal JD text
extraction → DeepSeek V4 Flash (latest).

## Files this area owns

- `packages/api/jdparser/llm/schemas.py` — **all** Pydantic models from SPEC §3
  (`ResumeProfile`, `ResumeEvidence`, `AdzunaQuery`, `JobRequirements`,
  `MetRequirement`, `FitJudgment`, `StoredResumeProfile`, `Fingerprint`,
  `FetchResult`, `QualityResult`, `EvaluatedJob`, `ErrorRecord`, `RunRecord`).
  These are shared across areas; this doc owns the file.
- `packages/api/jdparser/llm/client.py`
- `packages/api/jdparser/llm/resume_profiler.py`
- `packages/api/jdparser/llm/search_planner.py`
- `packages/api/jdparser/llm/jd_parser.py`
- `packages/api/jdparser/llm/fit_judge.py`
- tests: `tests/test_llm_schemas.py`

Must NOT edit `graph/`, `adzuna/`, `extract/`.

## `llm/client.py` — client + routing

```python
import os
import instructor
from openai import OpenAI
from jdparser.config import OPENROUTER_BASE_URL    # "https://openrouter.ai/api/v1"

_oai = OpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=os.environ["OPENROUTER_API_KEY"],
    default_headers={                              # optional OpenRouter attribution
        "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", ""),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "jdparser"),
    },
)
# instructor patches the client: chat.completions.create gains `response_model`
# (Pydantic) + `max_retries` (re-ask on validation failure). Mode.JSON is the most
# portable across heterogeneous OpenRouter models. If a specific model rejects JSON
# mode, fall back to instructor.Mode.MD_JSON for that model.
_client = instructor.from_openai(_oai, mode=instructor.Mode.JSON)


def get_client():
    return _client
```

Routing + per-call knobs come from `config.LLM_NODES` (SPEC §6.3) — one entry per
node (`profiler`/`planner`/`jd_parser`/`judge`), each carrying `model`,
`temperature`, `max_tokens`, `reasoning`. **Every field is env-overridable** at
startup (`LLM_MODEL_<NODE>` / `LLM_TEMP_<NODE>` / `LLM_MAX_TOKENS_<NODE>` /
`LLM_REASONING_<NODE>`); the defaults are baked into `config.py`. Agents pass their
`LLM_NODES[...]` entry to `_call`; do not hardcode model/tokens at the call site.

## Canonical call shape (kept identical across all six agents)

```python
# canonical — copy this shape; agents differ only by (cfg, schema, err_code, prompts)
import instructor
from jdparser.llm.client import get_client
from jdparser.config import JDParserError, LLM_MAX_RETRIES


def _reasoning_body(setting: str) -> dict:
    # OpenRouter normalizes `reasoning` across providers (GLM, Gemini, ...)
    if setting == "off":
        return {"reasoning": {"enabled": False}}
    return {"reasoning": {"effort": setting}}        # "low" | "medium" | "high"


def _hit_length(exc) -> bool:
    # best-effort: did the failed retry loop end on a length-truncation?
    comp = getattr(exc, "last_completion", None)
    try:
        return bool(comp and comp.choices and comp.choices[0].finish_reason == "length")
    except Exception:                                 # reason: defensive — never mask the real error
        return False


def _call(cfg: dict, system: str, user: str, schema, err_code: str):
    try:
        obj, completion = get_client().chat.completions.create_with_completion(
            model=cfg["model"],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],            # generous ceiling; billed only as generated
            response_model=schema,                   # instructor -> validated Pydantic instance
            max_retries=LLM_MAX_RETRIES,             # re-ask the model on a validation failure
            extra_body=_reasoning_body(cfg["reasoning"]),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except instructor.exceptions.InstructorRetryException as e:
        if _hit_length(e):                           # truncation, not a real schema problem
            raise JDParserError(code="LLM_TRUNCATED",
                                message=f"{err_code}: hit max_tokens; raise LLM_MAX_TOKENS_<NODE>")
        raise JDParserError(code=err_code, message=f"invalid structured output: {e}")
    except Exception as e:                            # openai.APIError / RateLimitError / provider error
        raise JDParserError(code="LLM_API_ERROR", message=str(e))
    # validated but the provider still flagged truncation -> surface it, don't return partial
    if completion.choices and completion.choices[0].finish_reason == "length":
        raise JDParserError(code="LLM_TRUNCATED",
                            message=f"{err_code}: finish_reason=length; raise LLM_MAX_TOKENS_<NODE>")
    return obj
```

Notes / locked decisions:
- **Per-node config is env-overridable (SPEC §6.3).** `_call` takes the
  `LLM_NODES[node]` dict, so an operator can retune model/temperature/`max_tokens`/
  reasoning per node via env without a code change. Defaults are generous on purpose
  (see SPEC §6.3 sizing rationale): `max_tokens` is a ceiling billed only as
  generated, and reasoning tokens draw from it.
- **`instructor` does the validation + retry.** `response_model=<PydanticClass>`
  returns an already-validated instance; on a schema mismatch it re-prompts with the
  validation error up to `max_retries`, then raises `InstructorRetryException` →
  mapped to the node's `err_code` (PROFILE_INVALID / PLAN_INVALID / PARSE_INVALID /
  JUDGE_INVALID, SPEC §6.4). `create_with_completion` returns `(obj, raw_completion)`
  so we can inspect `finish_reason`.
- **Truncation guard.** A `finish_reason == "length"` (on success or as the cause of
  a failed retry loop) surfaces `LLM_TRUNCATED` (SPEC §6.4), never a generic
  `*_INVALID` — so "the ceiling is too low" is diagnosable and the operator raises
  `LLM_MAX_TOKENS_<NODE>`. The generous defaults make this guard a rare backstop.
- **No provider-specific params.** No `thinking`/`effort`/assistant-prefill (Anthropic
  -only). Steer with the system prompt + `temperature`; control depth via `reasoning`.
- **`LLM_API_ERROR`** covers any OpenRouter transport/provider/rate-limit failure. The
  OpenAI SDK already retries transient 429/5xx (`max_retries` on the `OpenAI(...)`
  client, default 2) before this catch fires.
- Native OpenRouter structured outputs (`response_format={"type":"json_schema", ...}`)
  are the documented alternative if you ever drop the `instructor` dependency.

## `resume_profiler.py` — `_call(LLM_NODES["profiler"], …, ResumeProfile, "PROFILE_INVALID")`

Defaults (env-overridable, SPEC §6.3): DeepSeek V4 Flash (latest), temp 0.2, `max_tokens` 8000, reasoning off.

```python
def profile_resume(resume_text: str) -> ResumeProfile
```
System prompt requirements: extract `ResumeProfile` (SPEC §3.3). Emphasize: infer
`seniority`/`total_years_experience`/`domains`/`work_authorization` from the text;
**every** non-trivial claim AND every accomplishment/project/leadership bullet
gets a `ResumeEvidence` with a verbatim `source_quote` (one entry per claim, not
per theme — the judge cannot reread the resume); do not fabricate skills not
supported by the text. Truncate `resume_text` to `MAX_RESUME_CHARS` (SPEC §6.1)
before the call.

## `search_planner.py` — `_call(LLM_NODES["planner"], …, SearchPlan, "PLAN_INVALID")`

Defaults (env-overridable, SPEC §6.3): DeepSeek V4 Flash (latest), temp 0.3, `max_tokens` 4000, reasoning off.

```python
def plan_queries(profile: ResumeProfile) -> list[AdzunaQuery]
```
Returns `list[AdzunaQuery]`, length 1..`SEARCH_PLAN_MAX_QUERIES` (6). Because
`response_model` must be a single schema, define a wrapper model in `schemas.py`:

```python
class SearchPlan(BaseModel):
    queries: list[AdzunaQuery]
```
and the function returns `result.queries` (truncated to `SEARCH_PLAN_MAX_QUERIES`).
System prompt: expand role synonyms across `profile.roles`; map preferred locations
to `where` + `distance`; set employment-type booleans from `profile.employment_types`;
**only** use fields present in `AdzunaQuery` (SPEC §3.4) — the closed schema
(`extra="forbid"`) makes inventing params impossible (instructor will reject + re-ask),
but the prompt should reinforce it. If `queries == []`, the graph node raises
`PLAN_EMPTY` (SPEC §6.4); a structurally-invalid plan surfaces as `PLAN_INVALID`.

## `jd_parser.py` — `_call(LLM_NODES["jd_parser"], …, JobRequirements, "PARSE_INVALID")`

Defaults (env-overridable, SPEC §6.3): DeepSeek V4 Flash (latest), temp 0.1, `max_tokens` 6000, reasoning off.

```python
def parse_jd_requirements(jd_text: str) -> JobRequirements
```
Literal extraction (SPEC §3.5). System prompt: extract exactly what the JD states —
`required_skills` vs `preferred_skills` distinction matters; both are named
skills/tools/capabilities, not whole "Who You Are" bullets (split compound
bullets); `dealbreakers` are explicit hard filters (clearance, license,
on-site-only, citizenship); set `education_required` from must/required language.
Do not infer beyond the text. Truncate `jd_text` to `MAX_JD_CHARS` (60000).

## `fit_judge.py` — `_call(LLM_NODES["judge"], …, FitJudgment, "JUDGE_INVALID")`

Defaults (env-overridable, SPEC §6.3): DeepSeek V4 Flash (latest), temp 0.2, `max_tokens` 10000, reasoning
**medium** (the one node where reasoning earns its keep; its ceiling covers reasoning +
JSON — bumped from `low`: thematic/functional fit needs more than mechanical skill-list
matching).

```python
def judge_fit(profile: ResumeProfile, requirements: JobRequirements) -> FitJudgment
```
Logic decision (SPEC §3.6). System prompt: compare `profile.evidence`/skills
against `requirements`; `decision="qualified"` only if no `failed_dealbreakers`, no
`missing_hard_requirements`, and `thematic_fit` is true (the JD's actual function/subject
matter matches the candidate's target roles/domains — checked using `relevant_years_experience`,
not the domain-blind `profile.total_years_experience`, see SPEC §3.6); **cite** a
`MetRequirement.evidence_quote` (verbatim
resume span that actually supports the requirement — a leftover theme quote or
keyword collision is not support; unsupported conjuncts go in
`missing_hard_requirements`) for each required skill counted as met; set `confidence` honestly
(displayed jobs require ≥ 0.75, SPEC §6.1 — but the judge sets confidence on merit,
the gate is applied by code in `aggregate_matches`, SPEC §7). Pass both objects as
JSON in the user message.

## Done when

- `tests/test_llm_schemas.py`: every model in `schemas.py` round-trips
  `model_dump()` → `model_validate()`; `AdzunaQuery` rejects an unknown field
  (extra="forbid" — set `model_config = ConfigDict(extra="forbid")` on `AdzunaQuery`
  so the planner can't smuggle params); `FitJudgment` rejects `confidence` > 1.
- The four agent functions are import-clean and type-check (`uv run mypy --strict`).
- Config: with `LLM_MAX_TOKENS_JUDGE=123` and `LLM_REASONING_JUDGE=high` set,
  `config.LLM_NODES["judge"]` reflects `max_tokens==123` / `reasoning=="high"`;
  unset → defaults (10000 / `medium`). `_reasoning_body("off")` →
  `{"reasoning":{"enabled":False}}`; `_reasoning_body("low")` →
  `{"reasoning":{"effort":"low"}}`.
- Truncation guard: a faked completion with `finish_reason=="length"` raises
  `JDParserError(code="LLM_TRUNCATED")`, not a `*_INVALID`.
- A live smoke (gated behind `OPENROUTER_API_KEY`, run in M2 exit-check by the
  orchestrator, not the unit suite): `profile_resume(sample_text)` (DeepSeek V4 Flash (latest)) returns a
  valid `ResumeProfile` with ≥1 evidence entry; `parse_jd_requirements(sample_jd)`
  (DeepSeek V4 Flash (latest)) returns a valid `JobRequirements`; `judge_fit` on a contrived
  profile+requirements returns a `FitJudgment` whose `decision` is one of the three
  literals. Assert each call's `response.model` (or the OpenRouter response) reflects
  the routed slug.
