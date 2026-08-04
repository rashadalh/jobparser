"use client";

import { useState } from "react";
import { submitFeedback } from "@/lib/api";

/**
 * "The judge got this wrong" — for a qualified job (false positive) or a rejected one
 * (false negative). One component for both: the flow is identical and only the wording
 * differs, so the two call sites pass copy rather than each keeping their own state
 * machine to drift apart.
 *
 * Feedback applies to the candidate's NEXT run. Nothing already evaluated is re-judged,
 * which `doneText` says out loud so the user isn't left waiting for this page to change.
 */
export default function FeedbackForm({
  runId,
  jobId,
  openLabel,
  prompt,
  placeholder,
  doneText,
}: {
  runId: string;
  jobId: string;
  openLabel: string;
  prompt: string;
  placeholder: string;
  doneText: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "submitting" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!text.trim()) return;
    setState("submitting");
    setError(null);
    try {
      await submitFeedback(runId, jobId, text.trim());
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to submit feedback");
      setState("error");
    }
  }

  if (state === "done") {
    return <p className="text-sm text-gray-600">{doneText}</p>;
  }

  if (!open) {
    return (
      <button
        type="button"
        data-testid="feedback-open"
        onClick={() => setOpen(true)}
        className="text-xs font-medium text-gray-500 hover:text-gray-700"
      >
        {openLabel}
      </button>
    );
  }

  return (
    <div className="space-y-2">
      <label htmlFor={`feedback-${jobId}`} className="text-xs font-medium text-gray-600">
        {prompt}
      </label>
      <textarea
        id={`feedback-${jobId}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={state === "submitting"}
        rows={2}
        placeholder={placeholder}
        className="w-full rounded-md border border-gray-300 p-2 text-sm text-gray-800 disabled:opacity-50"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={state === "submitting" || !text.trim()}
          onClick={handleSubmit}
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
  );
}
