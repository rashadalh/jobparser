"""resume_profiler — `profile_resume` (logic node, GLM 5.2). SPEC §3.3 / §4.4.

Defaults (env-overridable, SPEC §6.3): GLM 5.2, temp 0.2, max_tokens 8000,
reasoning off. Maps validation failure -> PROFILE_INVALID (SPEC §6.4).
"""

from jdparser.config import LLM_NODES, MAX_RESUME_CHARS
from jdparser.llm.client import _call
from jdparser.llm.schemas import ResumeProfile

_SYSTEM = """\
You are a resume profiler. Read the candidate's resume and extract a single \
structured ResumeProfile JSON object.

Rules:
- Infer `seniority`, `domains`, and `work_authorization` from the text (do not leave \
them empty when the resume supports a value).
- `total_years_experience` is the candidate's TOTAL professional experience across \
their WHOLE career. COMPUTE it from the dated work history: span from the earliest \
professional role's start to the most recent role's end (or the present), and include \
EVERY role — even across a career change into a different field/industry. Do NOT just \
copy an "N years of experience" phrase from the summary when that phrase describes \
only one specialty (a career-changer's total is usually larger than any single \
field's tenure). When role dates are present, prefer computing from them over any \
self-described figure. Back this number with an `evidence` entry that cites the \
earliest and/or latest dated role it is based on.
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
    return _call(LLM_NODES["profiler"], _SYSTEM, user, ResumeProfile, "PROFILE_INVALID")
