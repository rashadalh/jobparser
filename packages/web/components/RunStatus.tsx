"use client";

import type { LlmUsage, RunRecord } from "@/lib/types";

// Sub-cent runs are the normal case, so two decimals would show every run as "$0.00".
// Four gets real resolution without pretending to more precision than exists.
function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return `$${usd.toFixed(4)}`;
}

function CostLine({ usage }: { usage: LlmUsage }) {
  const tokens = usage.prompt_tokens + usage.completion_tokens;
  return (
    <p data-testid="run-cost" className="mt-1 text-sm text-green-700">
      {usage.cost_complete ? "Cost" : "Cost at least"}{" "}
      <span className="font-medium">{formatCost(usage.cost_usd)}</span> across{" "}
      {usage.calls} model {usage.calls === 1 ? "call" : "calls"} and{" "}
      {tokens.toLocaleString()} tokens
      {usage.cost_complete ? "" : " (some calls didn't report a price)"}
    </p>
  );
}

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
        ? "Queued. Getting your run ready…"
        : run.phase ?? "Matching jobs against your resume…";
    // Show a progress bar once the eval fan-out size is known (jobs_total > 0).
    const showBar = run.jobs_total != null && run.jobs_total > 0;
    const done = run.jobs_done ?? 0;
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <div className="flex items-center gap-3">
          <Spinner />
          <span>{label}</span>
        </div>
        {showBar && (
          <div className="mt-3">
            <progress
              value={done}
              max={run.jobs_total!}
              className="h-2 w-full [&::-webkit-progress-bar]:rounded [&::-webkit-progress-bar]:bg-gray-100 [&::-webkit-progress-value]:rounded [&::-webkit-progress-value]:bg-gray-700 [&::-moz-progress-bar]:bg-gray-700"
            />
            <p className="mt-1 text-xs text-gray-500">
              Evaluated {done} of {run.jobs_total} jobs
            </p>
          </div>
        )}
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
        {run.usage && run.usage.calls > 0 && (
          <p data-testid="run-cost" className="mt-1 text-sm text-red-700">
            It still spent {formatCost(run.usage.cost_usd)} across {run.usage.calls}{" "}
            model {run.usage.calls === 1 ? "call" : "calls"} before failing.
          </p>
        )}
      </div>
    );
  }

  // completed
  const cacheMsg =
    run.resume_cache_hit === true
      ? "Reused your saved profile"
      : run.resume_cache_hit === false
        ? "Parsed your resume from scratch"
        : null;

  return (
    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
      <p className="text-sm font-semibold text-green-800">Run complete</p>
      {cacheMsg && <p className="mt-1 text-sm text-green-700">{cacheMsg}</p>}
      {run.usage && <CostLine usage={run.usage} />}
    </div>
  );
}
