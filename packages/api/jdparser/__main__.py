"""CLI entry — ``uv run python -m jdparser <resume_path> [--user local] [--json]``.

A dev/verification convenience and the Tier-2 harness (the browser flow is the
definition of done, SPEC §7.5). Builds an initial ``JobMatchState``, invokes the
LangGraph pipeline, and prints the qualified jobs (or the full result as JSON).

Run config (SPEC §3.10, §4.2): ``thread_id == run_id`` keys this run in the in-memory
``MemorySaver`` checkpointer; ``max_concurrency == EVAL_FANOUT_CONCURRENCY`` throttles
the fan-out ``Send`` branches.
"""

import argparse
import json
import sys
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from jdparser.config import EVAL_FANOUT_CONCURRENCY, JDParserError
from jdparser.graph.build import build_graph
from jdparser.graph.state import JobMatchState


def _initial_state(run_id: str, user_id: str, resume_file_path: str) -> JobMatchState:
    return {
        "run_id": run_id,
        "user_id": user_id,
        "resume_file_path": resume_file_path,
        "resume_text": None,
        "resume_fingerprint": None,
        "resume_profile_id": None,
        "resume_profile": None,
        "resume_cache_hit": False,
        "search_locations": None,
        "broaden_search": True,
        "search_plan": None,
        "adzuna_results": [],
        "deduped_jobs": [],
        "evaluated_jobs": [],
        "qualified_jobs": [],
        "errors": [],
    }


def _print_summary(result: dict[str, Any]) -> None:
    # cache_hit is grepped by the orchestrator's live smoke (lowercased true/false).
    cache_hit = str(bool(result.get("resume_cache_hit"))).lower()
    print(f"cache_hit={cache_hit}")
    qualified = result.get("qualified_jobs") or []
    print(f"qualified={len(qualified)}")
    for ej in qualified:
        judgment = ej.get("judgment") or {}
        met = judgment.get("met_requirements") or []
        evidence = met[0]["evidence_quote"] if met else ""
        print(f"- {ej.get('title')} @ {ej.get('company')}")
        print(f"  {ej.get('final_url')}")
        if evidence:
            print(f"  evidence: {evidence}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jdparser", description="Resume-driven job matcher")
    parser.add_argument("resume_path", help="path to a PDF/DOCX/TXT resume")
    parser.add_argument("--user", default="local", help="user id (MVP-fixed default 'local')")
    parser.add_argument("--json", action="store_true", dest="as_json", help="dump full result JSON")
    args = parser.parse_args(argv)

    run_id = str(uuid4())
    graph = build_graph()
    state = _initial_state(run_id, args.user, args.resume_path)
    config: RunnableConfig = {
        "max_concurrency": EVAL_FANOUT_CONCURRENCY,
        "configurable": {"thread_id": run_id},
    }
    try:
        result: dict[str, Any] = graph.invoke(state, config=config)
    except JDParserError as e:
        print(f"{e.code}: {e.message}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
