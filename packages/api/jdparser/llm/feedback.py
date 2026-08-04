"""feedback — `distill_notes` (logic node, gemini-3.1-flash-lite).

Merges a user's free-text correction on a QUALIFIED job ("I don't have an active
clearance") into the candidate's existing note list. NOT a blind append: the model
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
    existing: list[CandidateNote], job_context: dict[str, Any], feedback_text: str
) -> list[CandidateNote]:
    user = json.dumps(
        {
            "existing_notes": [n.model_dump() for n in existing],
            "job_context": job_context,
            "feedback": feedback_text,
        }
    )
    result = _call(LLM_NODES["feedback"], _SYSTEM, user, CandidateNotes, "FEEDBACK_INVALID")
    return result.notes
