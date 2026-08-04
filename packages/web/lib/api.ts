import type {
  CandidateNote,
  RunRecord,
  ProfileSummary,
  RunSummary,
  StoredResumeProfile,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const POLL_INTERVAL_MS = 2000;     // SPEC §6.2
export const POLL_TIMEOUT_MS = 300000;

export async function startRun(file: File): Promise<{ run_id: string }> {
  const fd = new FormData(); fd.append("file", file);
  const r = await fetch(`${BASE}/api/runs`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`start failed: ${r.status}`);
  return r.json();
}
// Start a run from an already-parsed resume (no upload, no re-parse). `locations`,
// when non-null, overrides the resume's inferred search locations for this run.
// `maxDaysOld` caps how old a listing may be (in days); 0 = any age. `includeAgencies`
// lets recruitment-agency listings past the relevance screen (default: screened out).
export async function startRunFromProfile(
  cacheKey: string,
  locations: string[] | null = null,
  broaden: boolean = true,
  maxDaysOld: number = 7,
  includeAgencies: boolean = false,
): Promise<{ run_id: string }> {
  const fd = new FormData();
  fd.append("profile_id", cacheKey);
  if (locations !== null) fd.append("locations", JSON.stringify(locations));
  fd.append("broaden", String(broaden));
  fd.append("max_days_old", String(maxDaysOld));
  fd.append("include_agencies", String(includeAgencies));
  const r = await fetch(`${BASE}/api/runs`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`start failed: ${r.status}`);
  return r.json();
}
// Parse a resume into a profile only (no job search). Caches it for later reuse.
export async function parseResume(file: File): Promise<StoredResumeProfile> {
  const fd = new FormData(); fd.append("file", file);
  const r = await fetch(`${BASE}/api/parse`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail = `parse failed: ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* keep the status-based message */
    }
    throw new Error(detail);
  }
  return r.json();
}
export async function getRun(runId: string): Promise<RunRecord> {
  const r = await fetch(`${BASE}/api/runs/${runId}`);
  if (!r.ok) throw new Error(`status failed: ${r.status}`);
  return r.json();
}
export async function listProfiles(): Promise<ProfileSummary[]> {
  const r = await fetch(`${BASE}/api/profiles`);
  if (!r.ok) throw new Error(`profiles failed: ${r.status}`);
  return r.json();
}
export async function listRuns(): Promise<RunSummary[]> {
  const r = await fetch(`${BASE}/api/runs`);
  if (!r.ok) throw new Error(`runs failed: ${r.status}`);
  return r.json();
}
// Report "this isn't actually a fit" on a QUALIFIED job. Distilled into the candidate's
// note list (keyed by resume cache_key) and applied on the candidate's NEXT run.
export async function submitFeedback(
  runId: string,
  jobId: string,
  text: string,
): Promise<CandidateNote[]> {
  const fd = new FormData();
  fd.append("run_id", runId);
  fd.append("job_id", jobId);
  fd.append("text", text);
  const r = await fetch(`${BASE}/api/feedback`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail = `feedback failed: ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* keep the status-based message */
    }
    throw new Error(detail);
  }
  return (await r.json()).notes;
}

// Add feedback about YOURSELF rather than about a specific job ("I won't relocate").
// Returns the full re-distilled note list, not just the new note.
export async function addProfileNote(
  cacheKey: string,
  text: string,
): Promise<CandidateNote[]> {
  const fd = new FormData();
  fd.append("text", text);
  const r = await fetch(`${BASE}/api/profiles/${cacheKey}/notes`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    let detail = `saving feedback failed: ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* keep the status-based message */
    }
    throw new Error(detail);
  }
  return (await r.json()).notes;
}

// Remove one note by its index in the STORED list (not the panel's display order).
// Returns the remaining notes; deletion is deterministic, with no re-distillation.
export async function deleteProfileNote(
  cacheKey: string,
  storedIndex: number,
): Promise<{ notes: CandidateNote[]; deleted: string }> {
  const r = await fetch(
    `${BASE}/api/profiles/${cacheKey}/notes/${storedIndex}`,
    { method: "DELETE" },
  );
  if (!r.ok) throw new Error(`removing the note failed: ${r.status}`);
  return r.json();
}
