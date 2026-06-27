"use client";

import { useEffect, useRef, useState } from "react";
import {
  startRun,
  getRun,
  POLL_INTERVAL_MS,
  POLL_TIMEOUT_MS,
} from "@/lib/api";
import type { RunRecord } from "@/lib/types";
import ResumeUpload from "@/components/ResumeUpload";
import RunStatus from "@/components/RunStatus";
import JobCard from "@/components/JobCard";
import FailuresPanel from "@/components/FailuresPanel";

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

  async function handleSubmit(file: File) {
    stopPolling();
    setError(null);
    setTimedOut(false);
    setRun(null);
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
      <ResumeUpload onSubmit={handleSubmit} disabled={active} />

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

      {done && run && run.status === "completed" && (
        <section>
          <h2 className="mb-3 text-lg font-semibold text-gray-900">
            Qualified jobs ({run.qualified_jobs.length})
          </h2>
          {run.qualified_jobs.length === 0 ? (
            <p className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600 shadow-sm">
              No qualifying jobs found for this resume. See the audit panel below
              for what was evaluated.
            </p>
          ) : (
            <div className="space-y-4">
              {run.qualified_jobs.map((job) => (
                <JobCard key={job.job_id} job={job} />
              ))}
            </div>
          )}
        </section>
      )}

      {done && run && (
        <FailuresPanel
          failures={run.failures}
          rejected={run.rejected}
          errors={run.errors}
        />
      )}
    </Shell>
  );
}
