"""resume_profiler — `profile_resume` (logic node, deepseek-v4-flash-latest). SPEC §3.3 / §4.4.

Defaults (env-overridable, SPEC §6.3): deepseek-v4-flash-latest, temp 0.2, max_tokens 8000,
reasoning off. Maps validation failure -> PROFILE_INVALID (SPEC §6.4).
"""

from jdparser.config import LLM_NODES, MAX_RESUME_CHARS
from jdparser.experience import current_decimal_year, total_years_experience
from jdparser.llm.client import _call
from jdparser.llm.prompts import load
from jdparser.llm.schemas import ResumeProfile

_SYSTEM = load("resume_profiler")


def profile_resume(resume_text: str) -> ResumeProfile:
    user = resume_text[:MAX_RESUME_CHARS]
    profile = _call(LLM_NODES["profiler"], _SYSTEM, user, ResumeProfile, "PROFILE_INVALID")
    # Step 2 (deterministic): recompute total_years_experience from the extracted
    # work_periods via interval union, overriding the LLM's estimate. Overlap- and
    # gap-correct; no LLM arithmetic. (No periods -> keep the LLM's value as a fallback.)
    if profile.work_periods:
        computed = total_years_experience(profile.work_periods, current_decimal_year())
        profile = profile.model_copy(update={"total_years_experience": computed})
    return profile
