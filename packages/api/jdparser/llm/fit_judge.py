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
You are a hiring-fit judge. Given a candidate ResumeProfile and a job's \
JobRequirements (both JSON), decide whether the candidate qualifies and return one \
FitJudgment JSON object.

Rules:
- Set `decision="qualified"` ONLY if BOTH hold: `failed_dealbreakers` is empty AND \
`missing_hard_requirements` is empty. If a dealbreaker is failed or a required \
skill is unmet, the decision is "not_qualified" (or "uncertain" when the evidence \
is genuinely ambiguous).
- For EACH required skill you count as met, add a `MetRequirement` whose \
`evidence_quote` is a VERBATIM span from the candidate's resume evidence/skills \
proving it — do not paraphrase or invent the quote.
- List unmet required skills in `missing_hard_requirements` and failed explicit \
hard filters in `failed_dealbreakers`.
- Set `confidence` honestly in [0.0, 1.0] based on the strength of the evidence; do \
not inflate it. (Code applies the display threshold separately.)
- `rationale` briefly explains the decision. Output ONLY fields defined by the \
FitJudgment schema.
"""


def judge_fit(profile: ResumeProfile, requirements: JobRequirements) -> FitJudgment:
    user = json.dumps(
        {
            "profile": profile.model_dump(),
            "requirements": requirements.model_dump(),
        }
    )
    return _call(LLM_NODES["judge"], _SYSTEM, user, FitJudgment, "JUDGE_INVALID")
