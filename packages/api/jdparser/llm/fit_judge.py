"""fit_judge — `judge_fit` (logic node, deepseek-v4-flash-latest). SPEC §3.6 / §4.4.

Defaults (env-overridable, SPEC §6.3): deepseek-v4-flash-latest, temp 0.2, max_tokens 10000,
reasoning medium (the one node where reasoning earns its keep — bumped from low: plain
skill/dealbreaker matching missed thematic mismatches, see THEMATIC FIT below). Maps
validation failure -> JUDGE_INVALID. The displayed-job confidence gate (>= 0.75) is
applied by code in aggregate_matches (SPEC §7); the judge only sets confidence on merit.
"""

import json

from jdparser.config import LLM_NODES
from jdparser.llm.client import _call
from jdparser.llm.prompts import load
from jdparser.llm.schemas import CandidateNote, FitJudgment, JobRequirements, ResumeProfile

_SYSTEM = load("fit_judge")


def judge_fit(
    profile: ResumeProfile,
    requirements: JobRequirements,
    job_title: str = "",
    notes: list[CandidateNote] | None = None,
) -> FitJudgment:
    user = json.dumps(
        {
            "job_title": job_title,
            "profile": profile.model_dump(),
            "requirements": requirements.model_dump(),
            "notes": [n.model_dump() for n in (notes or [])],
        }
    )
    return _call(LLM_NODES["judge"], _SYSTEM, user, FitJudgment, "JUDGE_INVALID")
