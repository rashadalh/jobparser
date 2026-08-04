# `llm/` — the six OpenRouter agents

Every LLM call in the system. Owning spec:
[`docs/IMPLEMENTATION_LLM.md`](../../../../docs/IMPLEMENTATION_LLM.md).

| Module | What it holds |
|---|---|
| `schemas.py` | **Every** Pydantic model in the system — the single owner (SPEC §3). |
| `client.py` | The OpenRouter client and `_call`, the one canonical call shape. |
| `prompts/` | One `.md` per node. The prose lives here, not in string literals. |
| `resume_profiler.py` `search_planner.py` `jd_parser.py` `fit_judge.py` `screener.py` `feedback.py` | One agent each: build the user message, call `_call`, map failure to a code. |

Each agent module is ~30 lines of Python around a much larger prompt. The prompt is the
substance — that is where the behavior actually lives, and where to look first.
