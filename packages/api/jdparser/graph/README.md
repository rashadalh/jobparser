# `graph/` — LangGraph orchestration

The pipeline itself: what runs, in what order, and what happens when a stage fails.
Owning spec: [`docs/IMPLEMENTATION_GRAPH.md`](../../../../docs/IMPLEMENTATION_GRAPH.md).

| Module | What it holds |
|---|---|
| `state.py` | The two TypedDict state schemas + `initial_state()`. Channels carry `.model_dump()` payloads, never live models. |
| `nodes.py` | The nine top-level nodes, the location-override algorithm, and `is_qualified` — the deterministic display gate. |
| `subgraph.py` | The per-job stage machine (resolve → fetch → extract → quality → parse → judge) and its two terminal nodes. |
| `build.py` | Wiring only. Both graphs assembled and compiled here; no business logic. |

Two things that look like mistakes and are not:
- `JobEvalState` re-declares `evaluated_jobs`/`errors`. Required — LangGraph only
  propagates a subgraph's keys to parent reducers if the subgraph's schema declares them.
- The subgraph's per-job field is `job`/`notes`, not `resume_profile`/`candidate_notes`.
  A name shared with a parent channel auto-propagates back and collides across concurrent
  `Send` branches.
