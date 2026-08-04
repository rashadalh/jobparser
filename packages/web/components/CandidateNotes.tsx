"use client";

import { useState } from "react";
import { addProfileNote } from "@/lib/api";
import type { CandidateNote } from "@/lib/types";

const KIND_LABEL: Record<string, string> = {
  dealbreaker: "Dealbreaker",
  preference: "Preference",
  context: "Background",
};

const KIND_STYLE: Record<string, string> = {
  dealbreaker: "bg-red-100 text-red-800",
  preference: "bg-amber-100 text-amber-800",
  context: "bg-blue-100 text-blue-800",
};

// Dealbreakers first: those are the ones that actually fail a job, so they matter most
// to a reader scanning the list.
const KIND_ORDER = ["dealbreaker", "preference", "context"];

/**
 * Everything the candidate has told the matcher about themselves, and a box to tell it
 * more.
 *
 * The list is already a summary. The server re-distills the whole thing on every
 * submission rather than appending, so a new note can merge with or replace an older one
 * it contradicts. That means the list can SHRINK after you add to it, which is correct
 * and worth not being surprised by.
 */
export default function CandidateNotes({
  cacheKey,
  notes,
  onNotesChange,
  disabled,
}: {
  cacheKey: string;
  notes: CandidateNote[];
  onNotesChange: (notes: CandidateNote[]) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const sorted = [...notes].sort(
    (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind),
  );

  async function handleAdd() {
    if (!text.trim()) return;
    setState("saving");
    setError(null);
    try {
      onNotesChange(await addProfileNote(cacheKey, text.trim()));
      setText("");
      setState("idle");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save your feedback");
      setState("error");
    }
  }

  return (
    <details data-testid="candidate-notes" className="mt-3">
      <summary className="cursor-pointer text-xs font-medium text-gray-600">
        What the matcher knows about you ({notes.length})
      </summary>

      <p className="mt-1 text-xs text-gray-400">
        Everything you&apos;ve told it, kept as a short summary rather than a running
        log. It applies to your next run, so nothing already evaluated changes.
      </p>

      {sorted.length === 0 ? (
        <p className="mt-2 text-xs text-gray-400">
          Nothing yet. Add something below, or use &ldquo;Not a fit?&rdquo; and
          &ldquo;Disagree with this?&rdquo; on a result to correct a specific job.
        </p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {sorted.map((n, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-gray-700">
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 font-medium ${
                  KIND_STYLE[n.kind] ?? "bg-gray-100 text-gray-700"
                }`}
              >
                {KIND_LABEL[n.kind] ?? n.kind}
              </span>
              <span>
                {n.note}
                {n.source ? (
                  <span className="ml-1 text-gray-400">({n.source})</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 space-y-2">
        <label htmlFor="candidate-note-input" className="text-xs font-medium text-gray-600">
          Anything else it should know?
        </label>
        <textarea
          id="candidate-note-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled || state === "saving"}
          rows={2}
          placeholder="e.g. I won't relocate outside Texas, and the 2019 gap was contract work"
          className="w-full rounded-md border border-gray-300 p-2 text-xs text-gray-800 disabled:opacity-50"
        />
        <button
          type="button"
          disabled={disabled || state === "saving" || !text.trim()}
          onClick={handleAdd}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {state === "saving" ? "Saving…" : "Add"}
        </button>
        {state === "error" && error && <p className="text-xs text-red-600">{error}</p>}
      </div>
    </details>
  );
}
