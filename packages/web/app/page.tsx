"use client";

import { useEffect, useRef, useState } from "react";
import {
  startRun,
  startRunFromProfile,
  parseResume,
  getRun,
  POLL_INTERVAL_MS,
  POLL_TIMEOUT_MS,
} from "@/lib/api";
import type { RunRecord, ResumeProfile } from "@/lib/types";
import ResumeUpload from "@/components/ResumeUpload";
import RunStatus from "@/components/RunStatus";
import QualifiedJobs from "@/components/QualifiedJobs";
import FailuresPanel from "@/components/FailuresPanel";
import ResumeProfileView from "@/components/ResumeProfileView";
import SavedPanel from "@/components/SavedPanel";

type Phase = "idle" | "starting" | "polling" | "done" | "error";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Resume Job Matcher</h1>
        <p className="mt-1 text-sm text-gray-600">
          Upload a resume — see only the jobs you qualify for, each backed by
          cited evidence.
        </p>
      </header>
      <div className="space-y-6">{children}</div>
    </main>
  );
}

export default function Home() {
  const [mounted, setMounted] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [run, setRun] = useState<RunRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0); // bump to re-fetch saved resumes + run history
  const [parsing, setParsing] = useState(false);
  const [parsedProfile, setParsedProfile] = useState<ResumeProfile | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const deadlineRef = useRef<number>(0);

  // Hydration gate: render a stable skeleton until the client has mounted, so
  // server output and the first client render match (BUILD.md hard rule).
  useEffect(() => setMounted(true), []);

  function stopPolling() {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }

  // Clear any live interval on unmount (no leaks).
  useEffect(() => stopPolling, []);

  function startPolling(id: string) {
    stopPolling();
    deadlineRef.current = Date.now() + POLL_TIMEOUT_MS;

    const tick = async () => {
      if (Date.now() > deadlineRef.current) {
        stopPolling();
        setTimedOut(true);
        return;
      }
      try {
        const rec = await getRun(id);
        setRun(rec);
        if (rec.status === "completed" || rec.status === "failed") {
          stopPolling();
          setPhase("done");
          setRefreshKey((k) => k + 1); // new run (+ maybe new parsed profile) now in the DB
        }
      } catch (e) {
        stopPolling();
        setError(e instanceof Error ? e.message : "Failed to fetch run status");
        setPhase("error");
      }
    };

    void tick(); // poll immediately, then on the interval
    intervalRef.current = setInterval(() => {
      void tick();
    }, POLL_INTERVAL_MS);
  }

  // Shared preamble for every action that supersedes what's on screen.
  //
  // `keepRun` is not incidental: handleOpenRun deliberately leaves the previous run
  // rendered while the next one loads, so the panel doesn't flash empty. That difference
  // was previously buried in four near-identical copies where it read as an oversight —
  // it's a choice, so it's a parameter.
  function resetForNewAction({ keepRun = false }: { keepRun?: boolean } = {}) {
    stopPolling();
    setError(null);
    setTimedOut(false);
    setParsedProfile(null);
    if (!keepRun) setRun(null);
  }

  async function handleSubmit(file: File) {
    resetForNewAction();
    setPhase("starting");
    try {
      const { run_id } = await startRun(file);
      setPhase("polling");
      startPolling(run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start run");
      setPhase("error");
    }
  }

  // Parse a resume into a profile ONLY — no job search (fast). Caches it so it can
  // then be reused for a search from the dropdown.
  async function handleParse(file: File) {
    resetForNewAction();
    setPhase("idle");   // parse-only: no run starts, so the phase stays idle
    setParsing(true);
    try {
      const stored = await parseResume(file);
      setParsedProfile(stored.profile);
      setRefreshKey((k) => k + 1); // now in the cache -> appears in the reuse dropdown
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to parse resume");
      setPhase("error");
    } finally {
      setParsing(false);
    }
  }

  // Start a fresh run from an already-parsed resume (no upload, no re-parse), with the
  // user's (possibly edited) search locations.
  async function handleRunFromProfile(
    cacheKey: string,
    locations: string[],
    broaden: boolean,
    maxDaysOld: number,
    includeAgencies: boolean,
  ) {
    resetForNewAction();
    setPhase("starting");
    try {
      const { run_id } = await startRunFromProfile(
        cacheKey,
        locations,
        broaden,
        maxDaysOld,
        includeAgencies,
      );
      setPhase("polling");
      startPolling(run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start run");
      setPhase("error");
    }
  }

  // Open a historical run from the DB and display it (poll only if still in flight).
  async function handleOpenRun(runId: string) {
    resetForNewAction({ keepRun: true });  // keep the current run visible while loading
    setPhase("starting");
    try {
      const rec = await getRun(runId);
      setRun(rec);
      if (rec.status === "completed" || rec.status === "failed") {
        setPhase("done");
      } else {
        setPhase("polling");
        startPolling(runId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to open run");
      setPhase("error");
    }
  }

  // Stable skeleton until mounted — keeps client-only state (File, timers) off
  // the server render path.
  if (!mounted) {
    return (
      <Shell>
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="h-5 w-40 rounded bg-gray-100" />
          <div className="mt-3 h-4 w-full rounded bg-gray-100" />
        </div>
      </Shell>
    );
  }

  const active = phase === "starting" || phase === "polling";
  const done = phase === "done";

  return (
    <Shell>
      <ResumeUpload
        onSubmit={handleSubmit}
        onParse={handleParse}
        disabled={active}
        parsing={parsing}
      />

      <SavedPanel
        refreshKey={refreshKey}
        disabled={active || parsing}
        onRunFromProfile={handleRunFromProfile}
        onOpenRun={handleOpenRun}
      />

      {parsing && (
        <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-600 shadow-sm">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
          Parsing your resume… (no job search)
        </div>
      )}

      {parsedProfile && !parsing && (
        <section className="space-y-2">
          <div className="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
            Resume parsed — <span className="font-medium">no job search run</span>. It&apos;s
            saved; pick it under &ldquo;Reuse a previously parsed resume&rdquo; to search
            without re-parsing.
          </div>
          <ResumeProfileView profile={parsedProfile} />
        </section>
      )}

      {phase === "error" && error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">Something went wrong</p>
          <p className="mt-1 text-sm text-red-700">{error}</p>
        </div>
      )}

      {(active || done) && <RunStatus run={run} active={active} />}

      {timedOut && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          This run is taking longer than expected. We&apos;ve stopped polling
          after {Math.round(POLL_TIMEOUT_MS / 1000)} seconds — it may still be
          running on the server. Try again later or re-upload.
        </div>
      )}

      {done && run && run.resume_profile && (
        <ResumeProfileView profile={run.resume_profile} />
      )}

      {done && run && run.status === "completed" && (
        <QualifiedJobs jobs={run.qualified_jobs} runId={run.run_id} />
      )}

      {done && run && (
        <FailuresPanel
          failures={run.failures}
          rejected={run.rejected}
          errors={run.errors}
          screened={run.screened_out ?? []}
          runId={run.run_id}
        />
      )}
    </Shell>
  );
}
