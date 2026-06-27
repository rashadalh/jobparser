import type { RunRecord } from "./types";

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
