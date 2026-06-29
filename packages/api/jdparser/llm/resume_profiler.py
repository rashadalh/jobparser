"""resume_profiler — `profile_resume` (logic node, GLM 5.2). SPEC §3.3 / §4.4.

Defaults (env-overridable, SPEC §6.3): GLM 5.2, temp 0.2, max_tokens 8000,
reasoning off. Maps validation failure -> PROFILE_INVALID (SPEC §6.4).
"""

from jdparser.config import LLM_NODES, MAX_RESUME_CHARS
from jdparser.experience import current_decimal_year, total_years_experience
from jdparser.llm.client import _call
from jdparser.llm.schemas import ResumeProfile

_SYSTEM = """\
You are a resume profiler. Read the candidate's resume and extract a single \
structured ResumeProfile JSON object.

Rules:
- Infer `seniority`, `domains`, and `work_authorization` from the text (do not leave \
them empty when the resume supports a value).
- `work_periods`: extract EVERY professional role with its dates as DECIMAL YEARS — \
`start_year` and `end_year` (examples: "Jan 2018" -> 2018.0, "Jul 2020" -> 2020.5, a \
year-only "2019" -> 2019.0). Use `end_year: null` for an ongoing/current role \
("Present"). Include every role across the whole career, even concurrent/overlapping \
ones and roles in a prior field before a career change. Be accurate and complete: the \
system computes total experience from these dates, so missing or wrong dates skew it.
- `total_years_experience`: give a rough estimate, but the system OVERWRITES it with a \
deterministic interval-union of `work_periods` (overlapping roles count once; gaps \
between jobs do not count) — so prioritise getting `work_periods` right over this \
number.
- `education` MUST list EVERY degree, diploma, or formal credential stated in the \
resume, each as a concise string (e.g. "M.S. Computer Science, MIT", "B.S. \
Mathematics"). Look in any Education/Academic section and inline mentions. If the \
resume truly states no education, use an empty list — but do not overlook a degree \
that is present.
- `roles` are normalized target roles (synonyms welcome); `skills` are concrete, \
named skills the resume actually demonstrates.
- `remote_preference` and `employment_types` reflect stated or strongly implied \
preferences; default to "any" / a sensible set only when the text gives a signal.
- Every non-trivial claim (seniority, years of experience, a key skill, a domain) \
MUST have a corresponding `evidence` entry whose `source_quote` is a VERBATIM span \
copied from the resume — do not paraphrase the quote.
- Do NOT fabricate skills, roles, or experience that the resume does not support. \
If the text does not support a claim, omit it.
- Output ONLY fields defined by the ResumeProfile schema.
"""


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
