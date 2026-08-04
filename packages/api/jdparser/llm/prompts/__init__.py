"""System prompts, one file per LLM node.

These are prose, not code, and they are the highest-churn lines in the repo — the model's
actual behavior lives here far more than in the ~10 lines of Python that call it. Kept as
`.md` files so a change shows up as a readable diff rather than a wall of backslash-
continued string literals.

Loaded via `importlib.resources`, so they resolve from the installed wheel as well as the
source tree — `pyproject.toml` includes them in the package data.
"""

from importlib.resources import files


def load(name: str) -> str:
    """Return the prompt text for ``name`` (e.g. "fit_judge").

    Read verbatim: no strip, no dedent. Trailing whitespace in a prompt is part of the
    prompt, and "helpfully" normalizing it changes what the model sees.
    """
    return (files(__package__) / f"{name}.md").read_text(encoding="utf-8")
