# IMPLEMENTATION_WEB — Next.js frontend

> Owns upload → poll → results/audit UI. References: SPEC §5 (API), §6.2, §9
> (disclose MVP stubs), §10 item 1 (snake_case types).

## Purpose

A single-page app: upload a resume, watch the run progress (polling), and render
qualified job cards with cited evidence plus an audit panel for failures and
rejections. Must disclose the MVP stubs it depends on (polling-not-push, single
local user) where relevant in copy.

## Files this area owns

- `packages/web/package.json` (+ generated `bun.lock`), `next.config.ts`, `tsconfig.json`, `postcss.config.mjs`
- `packages/web/app/globals.css`, `app/layout.tsx`, `app/page.tsx`
- `packages/web/components/ResumeUpload.tsx`, `RunStatus.tsx`, `JobCard.tsx`, `FailuresPanel.tsx`
- `packages/web/lib/api.ts`, `lib/types.ts`

Stack pins: SPEC/IMPLEMENTATION §Foundations — Next 16.2.9, React 19.2.7, TS 6,
Tailwind v4, `@types/{react,react-dom,node}` pinned exact (all **latest stable** on
npm as of 2026-06-26; Next 16 / React 19 are the newest stable majors — Next 17 /
React 20 do not exist).

Package manager: **bun**. Install with `bun install`; dev `bun run dev`; build
`bun run build`; typecheck `bunx tsc --noEmit`. Do not use `npm`/`npx` (bypasses
`bun.lock`). `package.json` `scripts` are the standard `next dev` / `next build` /
`next start`; bun runs them.

## Tailwind v4 setup (brittleness — do NOT scaffold a v3 config)

- `app/globals.css` starts with `@import "tailwindcss";` (no `@tailwind base/...`).
- `postcss.config.mjs`: `export default { plugins: { "@tailwindcss/postcss": {} } };`
- There is **no** `tailwind.config.js`. Content scanning is automatic in v4.

## `lib/types.ts` — mirror the API JSON (snake_case, SPEC §10 item 1)

```ts
export type FitDecision = "qualified" | "not_qualified" | "uncertain";
export type JobStatus = "qualified" | "not_qualified" | "uncertain" | "failed";

export interface MetRequirement { requirement: string; evidence_quote: string; }
export interface FitJudgment {
  decision: FitDecision; confidence: number;
  met_requirements: MetRequirement[]; missing_hard_requirements: string[];
  failed_dealbreakers: string[]; rationale: string;
}
export interface JobRequirements {
  required_skills: string[]; preferred_skills: string[];
  min_years_experience: number | null; education: string[];
  education_required: boolean; location_constraints: string[];
  remote_allowed: boolean | null; responsibilities: string[];
  dealbreakers: string[]; employment_type: string | null;
}
export interface EvaluatedJob {
  job_id: string; title: string; company: string; location: string;
  final_url: string | null; source: Record<string, unknown>;
  jd_char_len: number | null; requirements: JobRequirements | null;
  judgment: FitJudgment | null; status: JobStatus; failure_stage: string | null;
}
export interface ErrorRecord {
  job_id: string | null; stage: string; code: string;
  message: string; detail: Record<string, unknown> | null;
}
export type RunStatus = "pending" | "running" | "completed" | "failed";
export interface RunRecord {
  run_id: string; user_id: string; status: RunStatus;
  created_at: string; updated_at: string; resume_cache_hit: boolean | null;
  qualified_jobs: EvaluatedJob[]; failures: EvaluatedJob[];
  rejected: EvaluatedJob[]; errors: ErrorRecord[]; error: string | null;
}
```
Field names are snake_case to match the API exactly — no remap layer (SPEC §10).

## `lib/api.ts`

```ts
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const POLL_INTERVAL_MS = 2000;     // SPEC §6.2
export const POLL_TIMEOUT_MS = 300000;

export async function startRun(file: File): Promise<{ run_id: string }> {
  const fd = new FormData(); fd.append("file", file);
  const r = await fetch(`${BASE}/api/runs`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`start failed: ${r.status}`);
  return r.json();
}
export async function getRun(runId: string): Promise<RunRecord> {
  const r = await fetch(`${BASE}/api/runs/${runId}`);
  if (!r.ok) throw new Error(`status failed: ${r.status}`);
  return r.json();
}
```

## `app/page.tsx` (client component)

`"use client"`. Hydration-safe: gate browser-only state (`File`, polling timer)
behind a `mounted` flag — render a stable skeleton on the server, hydrate on the
client (BUILD.md hard rule). State machine:

```
idle ─upload─▶ starting ─run_id─▶ polling ─(status terminal)─▶ done | error
```
Poll loop: `setInterval(getRun, POLL_INTERVAL_MS)`, stop when
`status ∈ {completed, failed}` or `POLL_TIMEOUT_MS` elapsed; on timeout show a
"taking longer than expected" message (do not silently spin).

## Components

- **ResumeUpload** — file input (accept `.pdf,.docx,.txt`), submit → `startRun`.
- **RunStatus** — shows `pending/running` with a spinner; on `completed` shows
  `resume_cache_hit` ("Loaded your profile from cache" vs "Parsed a fresh profile").
- **JobCard** — for each `qualified_jobs[]`: company, title, a link to `final_url`,
  confidence, and a list of `judgment.met_requirements` (`requirement` + quoted
  `evidence_quote`). This is the cited-evidence display required by SPEC §7.5.
- **FailuresPanel** — collapsible audit view listing `failures[]` (per-job, with
  `failure_stage`), `rejected[]` (with `judgment.decision` + confidence), and
  `errors[]` (run-level `ErrorRecord`s such as `ADZUNA_HTTP`, shown by `stage`+`code`).
  Include one line of copy disclosing the MVP stubs it reflects: *"Results update by
  polling (no live stream); single local profile."* (SPEC §9.)

## Done when

- `bun run build` (in `packages/web`) succeeds; `bunx tsc --noEmit` clean (no `any`).
- Against a running API: uploading a resume transitions idle→polling→done and
  renders ≥1 `JobCard` with a clickable `final_url` and ≥1 cited evidence line;
  failures appear only in `FailuresPanel`, never the main feed.
- No hydration warnings in the console (client-only state is `mounted`-gated).
- This is the surface that satisfies the definition of done (SPEC §7.5, Tier-3).

## Feedback affordances

`components/FeedbackForm.tsx` is the single implementation of the "the judge got this
wrong" flow. Two call sites, differing only in copy:

| Where | Opens with | Records |
|---|---|---|
| `JobCard` (qualified) | "Not a fit?" | a false positive |
| `RejectedItem` (audit panel) | "Disagree with this?" | a false negative |

Both need `runId`, which is why `FailuresPanel` takes one and threads it to
`RejectedItem`. The server decides which correction it is from the job's bucket — the
client does not send a direction, so the two forms cannot disagree with the backend about
what they mean.

`FailedItem` deliberately has no form: there is no judgment to push back on.

## Candidate notes panel

`components/CandidateNotes.tsx`, rendered by `SavedPanel` once a saved resume is selected.
Shows the distilled feedback list and takes new feedback that isn't about any one job.

The list is already a summary — the server re-distills the whole thing on every
submission — so `onNotesChange` REPLACES local state with the server's returned list
rather than appending to it. Appending would show a note twice when the distiller merged
it into an existing one, and would hide the case where the list legitimately shrinks.

The panel sorts dealbreakers first, so display order is NOT storage order. Each row
carries the note's stored index through the sort and sends that to the delete endpoint.
Sending the display index would delete a different note than the one whose × was clicked,
silently and unrecoverably.
