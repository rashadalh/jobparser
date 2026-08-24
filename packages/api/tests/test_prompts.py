"""Prompt-loading guarantees (REFACTOR_AUDIT F8).

The prompts moved out of Python string literals into `jdparser/llm/prompts/*.md`. Two
things about that move can break silently — nothing else in the suite would notice,
because every other test fakes the LLM call out entirely:

1. A prompt file missing from the installed package. `.md` is not `.py`, so it ships only
   because pyproject lists it under `artifacts`; drop that line and every node raises at
   import time in the wheel while passing every test from the source tree.
2. Losing the planner's interpolation. `planner.md` is a TEMPLATE — if the `%` formatting
   is dropped, the prompt reaches the model with a literal `%(max_queries)d` in it and the
   planner quietly stops being told how many queries to produce.
"""

import pytest

from jdparser.config import ADZUNA_MAX_PAGES, SEARCH_PLAN_MAX_QUERIES
from jdparser.jobsource.adzuna import PLANNER_PROMPT
from jdparser.llm import feedback, fit_judge, jd_parser, resume_profiler, screener
from jdparser.llm.prompts import load

_NODES = {
    "fit_judge": fit_judge._SYSTEM,
    "screener": screener._SYSTEM,
    "resume_profiler": resume_profiler._SYSTEM,
    "jd_parser": jd_parser._SYSTEM,
    "feedback": feedback._SYSTEM,
}


@pytest.mark.parametrize("name", sorted(_NODES))
def test_every_node_prompt_loads_and_is_substantive(name: str) -> None:
    prompt = _NODES[name]
    assert prompt == load(name)          # the module holds exactly what the file holds
    assert len(prompt) > 500             # a truncated/empty read would still be a str


def test_planner_prompt_is_interpolated_not_raw() -> None:
    """The template's placeholders must be filled with the SAME budgets the code enforces."""
    assert "%(max_queries)d" in load("planner")      # the file is a template...
    assert "%(max_queries)d" not in PLANNER_PROMPT   # ...and the loaded prompt is not
    assert "%(max_pages)d" not in PLANNER_PROMPT
    assert f"{SEARCH_PLAN_MAX_QUERIES} queries" in PLANNER_PROMPT
    assert f"`pages` = {ADZUNA_MAX_PAGES}" in PLANNER_PROMPT


def test_prompts_are_read_verbatim() -> None:
    """No strip/dedent on load — trailing whitespace is part of the prompt."""
    assert load("fit_judge").endswith("\n")


def test_profiler_requires_per_bullet_evidence() -> None:
    """The judge cannot reread the resume; a handful of theme quotes starves it."""
    prompt = load("resume_profiler")
    assert "ONLY window" in prompt
    assert "one entry per bullet" in prompt


def test_judge_rejects_unsupported_citations() -> None:
    """A loosely related leftover quote must not count as proof."""
    prompt = load("fit_judge")
    assert "Sharing a word is not support" in prompt
    assert "Agentic engineering teams" in prompt
    assert "does not prove" in prompt
    assert "missing_hard_requirements" in prompt


def test_jd_parser_splits_compound_qualification_bullets() -> None:
    """Whole 'Who You Are' sentences cannot be proven by one resume quote."""
    prompt = load("jd_parser")
    assert "NOT entire" in prompt
    assert "Who You Are" in prompt
    assert '["Python", "data pipelines"' in prompt
