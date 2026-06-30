"""fit_judge — `judge_fit` (logic node, gemini-3.1-flash-lite). SPEC §3.6 / §4.4.

Defaults (env-overridable, SPEC §6.3): gemini-3.1-flash-lite, temp 0.2, max_tokens 10000,
reasoning low (the one node where reasoning earns its keep). Maps validation
failure -> JUDGE_INVALID. The displayed-job confidence gate (>= 0.75) is applied by
code in aggregate_matches (SPEC §7); the judge only sets confidence on merit.
"""

import json

from jdparser.config import LLM_NODES
from jdparser.llm.client import _call
from jdparser.llm.schemas import FitJudgment, JobRequirements, ResumeProfile

_SYSTEM = """\
You are a hiring-fit judge. Given a candidate ResumeProfile, the job's title, and the \
job's JobRequirements (all provided as JSON), decide whether the candidate qualifies \
and return one FitJudgment JSON object.

Rules:
- Set `decision="qualified"` when `failed_dealbreakers` is empty, \
`missing_hard_requirements` is empty, AND the role is not seniority-disqualified (see \
SENIORITY FIT). Use "not_qualified" when a hard requirement is missing, a dealbreaker \
fails, or the role is seniority-disqualified; use "uncertain" only when the evidence is \
genuinely ambiguous.
- SENIORITY FIT (asymmetric — read carefully): infer the role's level from the job title \
and requirements (`min_years_experience`, required education, the overall skill bar) and \
compare it to the candidate's `seniority` and `total_years_experience`.
  • UNDER-qualified — the role is clearly ABOVE the candidate (e.g. staff/principal/ \
director for an early-career candidate, or a `min_years_experience` the candidate does \
not meet): NOT a fit → `decision="not_qualified"`, explain the gap.
  • OVER-qualified — the candidate EXCEEDS the role's typical level: this is NOT a \
disqualifier. The tool helps the candidate find jobs they can DO; whether a step-down is \
worth applying to is THEIR choice, not yours. Reject for over-qualification ONLY when the \
role is UNAMBIGUOUSLY entry-level BY ITS OWN LABEL — an internship, co-op, apprenticeship, \
new-grad / early-career program, or a title/description explicitly marked "entry-level" \
or "junior". In that narrow case set `decision="not_qualified"` and say so. For ANY normal \
individual-contributor role (tester, analyst, specialist, coordinator, engineer) whose \
hard requirements the candidate meets, exceeding its typical seniority is FINE → \
`decision="qualified"`. Do NOT reject a normal IC role merely because the candidate has \
more years or a "lead"/"senior" title than the role names.
- JUDGE THE CURRENT PROFESSIONAL, NOT A PAST CAREER: assess fit from the candidate's \
CURRENT / primary field and demonstrated skills. Do NOT hold an unrelated PRIOR career \
they have moved on from against them (e.g. a former lighting designer now working in \
software QA is a QA professional — judge the QA fit, not the lighting background).
- For EACH required skill you count as met, add a `MetRequirement` whose \
`evidence_quote` is a VERBATIM span from the candidate's resume evidence/skills \
proving it — do not paraphrase or invent the quote.
- EDUCATION: when the job's `education_required` is true, check the candidate's \
`education` list (degrees/credentials). Treat the requirement as MET if the candidate \
holds a degree at or above the level the job asks for (a higher degree satisfies a \
lower requirement). Only put education in `missing_hard_requirements` if the \
candidate's `education` list genuinely lacks an adequate degree — never treat a \
populated `education` list as "no education provided".
- List unmet required skills in `missing_hard_requirements` and failed explicit \
hard filters in `failed_dealbreakers`.
- Set `confidence` honestly in [0.0, 1.0] based on the strength of the evidence; do \
not inflate it. (Code applies the display threshold separately.)
- `rationale` briefly explains the decision, including any seniority mismatch. Output \
ONLY fields defined by the FitJudgment schema.
"""


def judge_fit(
    profile: ResumeProfile,
    requirements: JobRequirements,
    job_title: str = "",
) -> FitJudgment:
    user = json.dumps(
        {
            "job_title": job_title,
            "profile": profile.model_dump(),
            "requirements": requirements.model_dump(),
        }
    )
    return _call(LLM_NODES["judge"], _SYSTEM, user, FitJudgment, "JUDGE_INVALID")
