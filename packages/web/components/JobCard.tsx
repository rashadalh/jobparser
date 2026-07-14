"use client";

import { useState } from "react";
import type { EvaluatedJob } from "@/lib/types";
import { submitFeedback } from "@/lib/api";

export default function JobCard({ job, runId }: { job: EvaluatedJob; runId: string }) {
  const j = job.judgment;
  const confidencePct = j ? Math.round(j.confidence * 100) : null;
  const isAgency = job.is_recruitment_agency === true;

  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "submitting" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmitFeedback() {
    if (!text.trim()) return;
    setState("submitting");
    setError(null);
    try {
      await submitFeedback(runId, job.job_id, text.trim());
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to submit feedback");
      setState("error");
    }
  }

  return (
    <article
      data-testid="job-card"
      className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">
            {job.title}
            {isAgency && (
              <span
                data-testid="agency-badge"
                title="This posting appears to be from a third-party recruitment/staffing agency, not the direct employer."
                className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 align-middle text-xs font-medium text-amber-800"
              >
                Agency
              </span>
            )}
          </h3>
          <p className="mt-0.5 text-sm text-gray-600">
            {job.company}
            {job.location ? ` · ${job.location}` : ""}
          </p>
        </div>
        {confidencePct !== null && (
          <span className="shrink-0 rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-800">
            {confidencePct}% confidence
          </span>
        )}
      </div>

      {job.final_url && (
        <a
          href={job.final_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-block text-sm font-medium text-blue-600 hover:text-blue-500"
        >
          View job posting →
        </a>
      )}

      {j && j.met_requirements.length > 0 && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Why you qualify
          </h4>
          <ul className="mt-2 space-y-3">
            {j.met_requirements.map((m, i) => (
              <li key={i}>
                <p className="text-sm font-medium text-gray-800">
                  {m.requirement}
                </p>
                <blockquote
                  data-testid="evidence"
                  className="mt-1 border-l-2 border-blue-300 bg-blue-50/50 py-1 pl-3 text-sm italic text-gray-700"
                >
                  &ldquo;{m.evidence_quote}&rdquo;
                </blockquote>
              </li>
            ))}
          </ul>
        </div>
      )}

      {j && j.relevant_years_experience != null && j.thematic_rationale && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Experience match
          </h4>
          <p className="mt-2 text-sm font-medium text-gray-800">
            {j.relevant_years_experience.toFixed(1)} years of relevant experience
          </p>
          <blockquote
            data-testid="thematic-rationale"
            className="mt-1 border-l-2 border-blue-300 bg-blue-50/50 py-1 pl-3 text-sm italic text-gray-700"
          >
            {j.thematic_rationale}
          </blockquote>
        </div>
      )}

      <div className="mt-4 border-t border-gray-100 pt-3">
        {state === "done" ? (
          <p className="text-sm text-gray-600">
            Noted — this will inform your next run for this resume.
          </p>
        ) : open ? (
          <div className="space-y-2">
            <label htmlFor={`feedback-${job.job_id}`} className="text-xs font-medium text-gray-600">
              What&apos;s wrong with this match?
            </label>
            <textarea
              id={`feedback-${job.job_id}`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={state === "submitting"}
              rows={2}
              placeholder="e.g. I don't have an active clearance"
              className="w-full rounded-md border border-gray-300 p-2 text-sm text-gray-800 disabled:opacity-50"
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={state === "submitting" || !text.trim()}
                onClick={handleSubmitFeedback}
                className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {state === "submitting" ? "Submitting…" : "Submit"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  setError(null);
                }}
                className="rounded-md px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
            </div>
            {state === "error" && error && <p className="text-xs text-red-600">{error}</p>}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="text-xs font-medium text-gray-500 hover:text-gray-700"
          >
            Not a fit?
          </button>
        )}
      </div>
    </article>
  );
}
