"""jd_parser — `parse_jd_requirements` (text extraction, Gemini 3.1 Flash Lite).

SPEC §3.5 / §4.4. Defaults (env-overridable, SPEC §6.3): Gemini 3.1 Flash Lite,
temp 0.1, max_tokens 6000, reasoning off. Maps validation failure -> PARSE_INVALID.
"""

from jdparser.config import LLM_NODES, MAX_JD_CHARS
from jdparser.llm.client import _call
from jdparser.llm.schemas import JobRequirements

_SYSTEM = """\
You extract structured requirements from a single job description. Return one \
JobRequirements JSON object containing ONLY what the text literally states.

Rules:
- LITERAL extraction only — do NOT infer, generalize, or add requirements the JD \
does not state.
- `required_skills` are skills the JD marks as required/must-have; \
`preferred_skills` are nice-to-have / preferred / bonus. Keep them distinct.
- `dealbreakers` are EXPLICIT hard filters stated by the JD (e.g. active security \
clearance, professional license, on-site-only, citizenship/visa restriction). Only \
include something here if the JD states it as a hard condition.
- Set `education_required` to true ONLY when the JD uses must/required language for \
education; otherwise false (degrees listed as preferred go in `education` with \
`education_required` false).
- `min_years_experience`, `remote_allowed`, and `employment_type` are null unless \
the JD states them.
- Output ONLY fields defined by the JobRequirements schema.
"""


def parse_jd_requirements(jd_text: str) -> JobRequirements:
    user = jd_text[:MAX_JD_CHARS]
    return _call(LLM_NODES["jd_parser"], _SYSTEM, user, JobRequirements, "PARSE_INVALID")
