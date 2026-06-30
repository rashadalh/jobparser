"use client";

import { useEffect, useState } from "react";
import { listProfiles, listRuns } from "@/lib/api";
import type { ProfileSummary, RunSummary } from "@/lib/types";

function profileLabel(p: ProfileSummary): string {
  const role = p.roles[0] ?? "resume";
  const edu = p.education[0] ? ` · ${p.education[0]}` : "";
  return `${p.seniority} · ${role}${edu}`;
}

function fmtTime(iso: string): string {
  // client-only component (rendered after mount), so locale formatting is hydration-safe
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const STATUS_COLOR: Record<string, string> = {
  completed: "text-green-700",
  failed: "text-red-700",
  running: "text-blue-700",
  pending: "text-gray-500",
};

export default function SavedPanel({
  refreshKey,
  disabled,
  onRunFromProfile,
  onOpenRun,
}: {
  refreshKey: number;
  disabled: boolean;
  onRunFromProfile: (
    cacheKey: string,
    locations: string[],
    broaden: boolean,
    maxDaysOld: number,
  ) => void;
  onOpenRun: (runId: string) => void;
}) {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [locations, setLocations] = useState<string[]>([]); // editable search locations
  const [locInput, setLocInput] = useState<string>("");
  const [broaden, setBroaden] = useState<boolean>(true); // include nationwide results
  const [maxDaysOld, setMaxDaysOld] = useState<number>(7); // listing-age cap in days (0 = any)

  function selectProfile(key: string) {
    setSelected(key);
    const p = profiles.find((x) => x.cache_key === key);
    setLocations(p ? [...p.locations] : []); // pre-fill with the inferred locations
    setLocInput("");
    setBroaden(true);
    setMaxDaysOld(7); // default: only listings from the past week
  }
  function addLocation() {
    const v = locInput.trim();
    if (v && !locations.includes(v)) setLocations([...locations, v]);
    setLocInput("");
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([listProfiles(), listRuns()])
      .then(([p, r]) => {
        if (!cancelled) {
          setProfiles(p);
          setRuns(r);
        }
      })
      .catch(() => {
        /* best-effort: the panel just stays empty if the API is unreachable */
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (profiles.length === 0 && runs.length === 0) return null;

  return (
    <div className="space-y-3">
      {profiles.length > 0 && (
        <div
          data-testid="resume-picker"
          className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
        >
          <label className="text-sm font-medium text-gray-700">
            Reuse a previously parsed resume{" "}
            <span className="font-normal text-gray-500">
              (no re-upload, no re-parse — runs a fresh job search)
            </span>
          </label>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              value={selected}
              disabled={disabled}
              onChange={(e) => selectProfile(e.target.value)}
              className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 disabled:opacity-50"
            >
              <option value="">Choose a saved resume…</option>
              {profiles.map((p) => (
                <option key={p.cache_key} value={p.cache_key}>
                  {profileLabel(p)}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!selected || disabled}
              onClick={() => selected && onRunFromProfile(selected, locations, broaden, maxDaysOld)}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              Find matching jobs
            </button>
          </div>

          {selected && (
            <div className="mt-3" data-testid="location-editor">
              <label className="text-xs font-medium text-gray-600">
                Locations to search{" "}
                <span className="font-normal text-gray-400">
                  — pre-filled from the resume; add or remove, or clear all for a
                  nationwide search
                </span>
              </label>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                {locations.map((loc) => (
                  <span
                    key={loc}
                    className="flex items-center gap-1 rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                  >
                    {loc}
                    <button
                      type="button"
                      aria-label={`Remove ${loc}`}
                      disabled={disabled}
                      onClick={() => setLocations(locations.filter((l) => l !== loc))}
                      className="text-gray-400 hover:text-gray-800 disabled:opacity-50"
                    >
                      ×
                    </button>
                  </span>
                ))}
                {locations.length === 0 && (
                  <span className="text-xs text-gray-400">nationwide (no location filter)</span>
                )}
                <input
                  value={locInput}
                  disabled={disabled}
                  onChange={(e) => setLocInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addLocation();
                    }
                  }}
                  placeholder="Add a city/state…"
                  className="min-w-[9rem] flex-1 rounded border border-gray-300 px-2 py-1 text-xs disabled:opacity-50"
                />
                <button
                  type="button"
                  disabled={disabled || !locInput.trim()}
                  onClick={addLocation}
                  className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
                >
                  Add
                </button>
              </div>

              <label className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={broaden}
                  disabled={disabled}
                  onChange={(e) => setBroaden(e.target.checked)}
                  className="h-3.5 w-3.5"
                />
                Include broader results (also search nationwide)
                <span className="text-gray-400">
                  — uncheck to restrict strictly to the locations above
                </span>
              </label>

              <label
                data-testid="age-filter"
                className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-gray-600"
              >
                Posted within
                <select
                  value={maxDaysOld}
                  disabled={disabled}
                  onChange={(e) => setMaxDaysOld(Number(e.target.value))}
                  className="rounded border border-gray-300 px-2 py-1 text-xs disabled:opacity-50"
                >
                  <option value={1}>24 hours</option>
                  <option value={3}>3 days</option>
                  <option value={7}>1 week</option>
                  <option value={14}>2 weeks</option>
                  <option value={30}>1 month</option>
                  <option value={0}>Any time</option>
                </select>
                <span className="text-gray-400">— defaults to the past week</span>
              </label>
            </div>
          )}
        </div>
      )}

      {runs.length > 0 && (
        <details
          data-testid="run-history"
          className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
        >
          <summary className="cursor-pointer text-sm font-medium text-gray-700">
            Run history ({runs.length})
          </summary>
          <ul className="mt-2 divide-y divide-gray-100">
            {runs.map((r) => (
              <li key={r.run_id}>
                <button
                  type="button"
                  onClick={() => onOpenRun(r.run_id)}
                  className="flex w-full flex-wrap items-baseline gap-x-2 py-2 text-left text-sm hover:bg-gray-50"
                >
                  <span className="text-gray-500">{fmtTime(r.created_at)}</span>
                  <span
                    className={`font-medium ${STATUS_COLOR[r.status] ?? "text-gray-700"}`}
                  >
                    {r.status}
                  </span>
                  {r.status === "completed" && (
                    <span className="text-gray-600">
                      · {r.qualified_count} qualified · {r.rejected_count} rejected ·{" "}
                      {r.failed_count} failed
                    </span>
                  )}
                  {r.roles.length > 0 && (
                    <span className="text-gray-400">· {r.roles.join(", ")}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
