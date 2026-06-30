"""Graph assembly — SPEC §4.2. Compiles the per-job subgraph and the top-level graph.

``build_subgraph`` wires the ``JobEvalState`` evaluation graph (entry ``resolve_url``,
a per-stage conditional edge that routes to ``record_failure`` on a failure marker,
``finalize`` / ``record_failure`` → END). ``build_graph`` wires the top-level
``JobMatchState`` nodes, adds the compiled subgraph as the ``job_eval`` node, fans out
via ``Send`` (``evaluate_jobs``), joins at ``aggregate_matches``, and compiles with an
in-memory ``MemorySaver`` checkpointer (SPEC §9 MVP).

The caller passes the concurrency cap + thread id at invoke:
``config={"max_concurrency": EVAL_FANOUT_CONCURRENCY, "configurable": {"thread_id": run_id}}``
— LangGraph throttles concurrent ``Send`` branches to ``EVAL_FANOUT_CONCURRENCY``.
"""

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from jdparser.graph.nodes import (
    aggregate_matches,
    dedupe_jobs,
    evaluate_jobs,
    screen_jobs,
    extract_resume_text,
    fingerprint_resume,
    load_or_parse_profile,
    plan_searches,
    run_adzuna_search,
)
from jdparser.graph.state import JobEvalState, JobMatchState
from jdparser.graph.subgraph import (
    check_jd,
    extract_jd,
    fetch_page,
    finalize,
    judge_fit_node,
    parse_requirements,
    record_failure,
    resolve_url,
    route_after_check,
    route_after_extract,
    route_after_fetch,
    route_after_judge,
    route_after_parse,
    route_after_resolve,
)

# reason: LangGraph compiled-graph generics are runtime-irrelevant to callers (SPEC §4.2)
CompiledGraph = CompiledStateGraph[Any, Any, Any, Any]


def build_subgraph() -> CompiledGraph:
    g = StateGraph(JobEvalState)
    g.add_node("resolve_url", resolve_url)
    g.add_node("fetch_page", fetch_page)
    g.add_node("extract_jd", extract_jd)
    g.add_node("check_jd", check_jd)
    g.add_node("parse_requirements", parse_requirements)
    g.add_node("judge_fit", judge_fit_node)      # node "judge_fit" runs judge_fit_node (§4.3)
    g.add_node("finalize", finalize)
    g.add_node("record_failure", record_failure)

    g.add_edge(START, "resolve_url")
    # each non-terminal stage -> next | record_failure (router on s["result"])
    g.add_conditional_edges("resolve_url", route_after_resolve, ["fetch_page", "record_failure"])
    g.add_conditional_edges("fetch_page", route_after_fetch, ["extract_jd", "record_failure"])
    g.add_conditional_edges("extract_jd", route_after_extract, ["check_jd", "record_failure"])
    g.add_conditional_edges("check_jd", route_after_check, ["parse_requirements", "record_failure"])
    g.add_conditional_edges("parse_requirements", route_after_parse, ["judge_fit", "record_failure"])
    g.add_conditional_edges("judge_fit", route_after_judge, ["finalize", "record_failure"])
    g.add_edge("finalize", END)
    g.add_edge("record_failure", END)
    return g.compile()


def build_graph() -> CompiledGraph:
    sub = build_subgraph()
    g = StateGraph(JobMatchState)
    g.add_node("extract_resume_text", extract_resume_text)
    g.add_node("fingerprint_resume", fingerprint_resume)
    g.add_node("load_or_parse_profile", load_or_parse_profile)
    g.add_node("plan_searches", plan_searches)
    g.add_node("run_adzuna_search", run_adzuna_search)
    g.add_node("dedupe_jobs", dedupe_jobs)
    g.add_node("screen_jobs", screen_jobs)       # coarse same-field relevance filter
    g.add_node("job_eval", sub)                  # compiled subgraph as a node (Send targets it)
    g.add_node("aggregate_matches", aggregate_matches)

    g.add_edge(START, "extract_resume_text")
    g.add_edge("extract_resume_text", "fingerprint_resume")
    g.add_edge("fingerprint_resume", "load_or_parse_profile")
    g.add_edge("load_or_parse_profile", "plan_searches")
    g.add_edge("plan_searches", "run_adzuna_search")
    g.add_edge("run_adzuna_search", "dedupe_jobs")
    g.add_edge("dedupe_jobs", "screen_jobs")     # relevance pre-screen before the fan-out
    g.add_conditional_edges("screen_jobs", evaluate_jobs, ["job_eval"])  # fan-out over screened jobs
    g.add_edge("job_eval", "aggregate_matches")  # join (LangGraph waits for all Sends)
    g.add_edge("aggregate_matches", END)
    return g.compile(checkpointer=MemorySaver())  # MVP in-memory (SPEC §9)
