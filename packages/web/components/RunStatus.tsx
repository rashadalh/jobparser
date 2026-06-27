"use client";

import type { RunRecord } from "@/lib/types";

function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-gray-700"
    />
  );
}

export default function RunStatus({
  run,
  active,
}: {
  run: RunRecord | null;
  active: boolean;
}) {
  // No run record yet but a run is being started.
  if (!run) {
    if (!active) return null;
    return (
      <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <Spinner />
        <span>Starting run…</span>
      </div>
    );
  }

  if (run.status === "pending" || run.status === "running") {
    const label =
      run.status === "pending"
        ? "Queued — preparing your run…"
        : "Matching jobs against your resume…";
    return (
      <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <Spinner />
        <span>{label}</span>
      </div>
    );
  }

  if (run.status === "failed") {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-sm font-semibold text-red-800">Run failed</p>
        {run.error && (
          <p className="mt-1 text-sm text-red-700">{run.error}</p>
        )}
      </div>
    );
  }

  // completed
  const cacheMsg =
    run.resume_cache_hit === true
      ? "Loaded your profile from cache"
      : run.resume_cache_hit === false
        ? "Parsed a fresh profile"
        : null;

  return (
    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
      <p className="text-sm font-semibold text-green-800">Run complete</p>
      {cacheMsg && <p className="mt-1 text-sm text-green-700">{cacheMsg}</p>}
    </div>
  );
}
