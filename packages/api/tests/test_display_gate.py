"""The deterministic display gate — SPEC §7.1–§7.3.

`is_qualified` is the final filter standing between an evaluated job and the user's
feed, and it re-checks every condition independently of `status` so a stale status can
never leak a job through. These are its unit gates.
"""

from graph_harness import _eval_state, _req, _valid_ej
from jdparser.config import MIN_JD_CHARS
from jdparser.graph.nodes import is_qualified
from jdparser.graph.subgraph import _evaluated_job


# --- §7.1: is_qualified unit gates -------------------------------------------
def test_is_qualified_units() -> None:
    assert is_qualified(_valid_ej()) is True

    # null requirements excludes even with a qualified judgment present
    ej = _valid_ej()
    ej["requirements"] = None
    assert is_qualified(ej) is False

    # null / empty final_url excludes
    assert is_qualified({**_valid_ej(), "final_url": None}) is False
    assert is_qualified({**_valid_ej(), "final_url": ""}) is False

    # jd_char_len below MIN_JD_CHARS excludes
    assert is_qualified({**_valid_ej(), "jd_char_len": MIN_JD_CHARS - 1}) is False
    assert is_qualified({**_valid_ej(), "jd_char_len": None}) is False


# --- §7.2: dealbreaker / missing-hard-requirement gate -----------------------
def test_fit_gate_excludes_dealbreakers_and_missing() -> None:
    ej = _valid_ej()  # decision qualified, confidence 0.9
    ej["judgment"]["failed_dealbreakers"] = ["active TS/SCI clearance"]
    assert is_qualified(ej) is False

    ej2 = _valid_ej()
    ej2["judgment"]["missing_hard_requirements"] = ["kubernetes"]
    assert is_qualified(ej2) is False


# --- §7.3: confidence gate via _evaluated_job status derivation --------------
def test_confidence_gate() -> None:
    low = _evaluated_job(_eval_state("qualified", 0.6))  # type: ignore[arg-type]  # reason: JobEvalState payload
    assert low["status"] == "uncertain"
    assert is_qualified(low) is False

    high = _evaluated_job(_eval_state("qualified", 0.8))  # type: ignore[arg-type]  # reason: JobEvalState payload
    assert high["status"] == "qualified"
    assert is_qualified(high) is True
