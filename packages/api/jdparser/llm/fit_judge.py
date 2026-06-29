"""fit_judge — `judge_fit` (logic node, GLM 5.2). SPEC §3.6 / §4.4.

Defaults (env-overridable, SPEC §6.3): GLM 5.2, temp 0.2, max_tokens 10000,
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
- Set `decision="qualified"` ONLY if ALL of these hold: `failed_dealbreakers` is \
empty, `missing_hard_requirements` is empty, AND the role's seniority level is \
appropriate for the candidate (see SENIORITY FIT). Otherwise the decision is \
"not_qualified" (or "uncertain" only when the evidence is genuinely ambiguous).
- SENIORITY FIT (critical): infer the role's level from the job title and the \
requirements (`min_years_experience`, required education, the overall skill bar) and \
compare it to the candidate's `seniority` and `total_years_experience`. If the role \
is clearly BELOW the candidate — an internship, co-op, apprenticeship, \
new-grad/early-career, entry-level, or (for a mid/senior-or-above candidate) a junior \
role — then the candidate is OVER-QUALIFIED and it is NOT an appropriate match: set \
`decision="not_qualified"` and explain the level mismatch in `rationale`. A role far \
ABOVE the candidate's demonstrated level (e.g. staff/principal/director for an \
early-career candidate) is likewise not a fit. A modest one-level gap is acceptable. \
IMPORTANT: trivially MEETING an entry-level role's minimal requirements does NOT make \
it a good match — an over-qualified candidate does NOT qualify for that role.
- For EACH required skill you count as met, add a `MetRequirement` whose \
`evidence_quote` is a VERBATIM span from the candidate's resume evidence/skills \
proving it — do not paraphrase or invent the quote.
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
