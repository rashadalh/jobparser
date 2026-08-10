"""jd_parser — `parse_jd_requirements` (text extraction, DeepSeek V4 Flash (latest)).

SPEC §3.5 / §4.4. Defaults (env-overridable, SPEC §6.3): DeepSeek V4 Flash (latest),
temp 0.1, max_tokens 6000, reasoning off. Maps validation failure -> PARSE_INVALID.
"""

from jdparser.config import LLM_NODES, MAX_JD_CHARS
from jdparser.llm.client import _call
from jdparser.llm.prompts import load
from jdparser.llm.schemas import JobRequirements

_SYSTEM = load("jd_parser")


def parse_jd_requirements(jd_text: str) -> JobRequirements:
    user = jd_text[:MAX_JD_CHARS]
    return _call(LLM_NODES["jd_parser"], _SYSTEM, user, JobRequirements, "PARSE_INVALID")
