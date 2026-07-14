"""feedback tests — `distill_notes` (llm/feedback.py). ZERO live network: `_call` is
monkeypatched in the namespace where it's used, matching this repo's convention
(see test_graph_smoke.py's docstring)."""

import pytest

from jdparser.llm.feedback import distill_notes
from jdparser.llm.schemas import CandidateNote, CandidateNotes


def _existing() -> list[CandidateNote]:
    return [CandidateNote(note="Prefers remote", kind="preference", source="feedback on 'Old Co'")]


def test_distill_notes_returns_the_call_result(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = CandidateNotes(
        notes=[
            CandidateNote(
                note="No active security clearance",
                kind="dealbreaker",
                source="feedback on 'Acme SWE'",
            ),
            CandidateNote(note="Prefers remote", kind="preference", source="feedback on 'Old Co'"),
        ]
    )
    captured: dict[str, object] = {}

    def _fake_call(cfg, system, user, schema, err_code):  # type: ignore[no-untyped-def]
        captured["schema"] = schema
        captured["err_code"] = err_code
        captured["user"] = user
        return canned

    monkeypatch.setattr("jdparser.llm.feedback._call", _fake_call)

    job_context = {"title": "SWE @ Acme", "requirements": {"dealbreakers": ["active clearance"]}, "rationale": "strong fit"}
    result = distill_notes(_existing(), job_context, "I don't have an active clearance")

    assert result == canned.notes           # not a blind append — the model's full replacement list
    assert captured["schema"] is CandidateNotes
    assert captured["err_code"] == "FEEDBACK_INVALID"
    assert "active clearance" in str(captured["user"])  # feedback text made it into the payload


def test_distill_notes_maps_call_failure_to_jdparser_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from jdparser.config import JDParserError

    def _boom(cfg, system, user, schema, err_code):  # type: ignore[no-untyped-def]
        raise JDParserError(code=err_code, message="invalid structured output")

    monkeypatch.setattr("jdparser.llm.feedback._call", _boom)

    with pytest.raises(JDParserError) as ei:
        distill_notes([], {"title": "x"}, "feedback")
    assert ei.value.code == "FEEDBACK_INVALID"
