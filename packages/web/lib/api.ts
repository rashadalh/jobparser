import type {
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
// Start a run from an already-parsed resume (no upload, no re-parse).
export async function startRunFromProfile(
  cacheKey: string,
): Promise<{ run_id: string }> {
  const fd = new FormData(); fd.append("profile_id", cacheKey);
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
