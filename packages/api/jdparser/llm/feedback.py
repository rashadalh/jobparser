"""feedback — `distill_notes` (logic node, gemini-3.1-flash-lite).

Merges a user's free-text feedback into the candidate's existing note list — either a
correction on a job the judge got wrong, or something they volunteer about themselves
with no job attached (``job_context=None``). NOT a blind append: the model
consolidates near-duplicates and lets new feedback supersede/drop a contradicted old
note, so the list stays a small, current set of corrections rather than a growing log.
Maps validation failure -> FEEDBACK_INVALID.
"""

import json
from typing import Any

from jdparser.config import LLM_NODES
from jdparser.llm.client import _call
from jdparser.llm.prompts import load
from jdparser.llm.schemas import CandidateNote, CandidateNotes

_SYSTEM = load("feedback")


def distill_notes(
    existing: list[CandidateNote],
    job_context: dict[str, Any] | None,
    feedback_text: str,
) -> list[CandidateNote]:
    """Merge ``feedback_text`` into ``existing`` and return the FULL replacement list.

    ``job_context`` is ``None`` for feedback the candidate volunteers about themselves
    rather than about a job the judge got wrong ("I won't relocate"). The prompt branches
    on its absence; everything else about the merge is identical.
    """
    user = json.dumps(
        {
            "existing_notes": [n.model_dump() for n in existing],
            "job_context": job_context,
            "feedback": feedback_text,
        }
    )
    result = _call(LLM_NODES["feedback"], _SYSTEM, user, CandidateNotes, "FEEDBACK_INVALID")
    return result.notes
