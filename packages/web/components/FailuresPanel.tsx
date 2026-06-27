import type { EvaluatedJob, ErrorRecord } from "@/lib/types";

export default function FailuresPanel({
  failures,
  rejected,
  errors,
}: {
  failures: EvaluatedJob[];
  rejected: EvaluatedJob[];
  errors: ErrorRecord[];
}) {
  return (
    <details
      data-testid="failures-panel"
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
    >
      <summary className="cursor-pointer text-sm font-medium text-gray-700">
        Audit — {failures.length} failed · {rejected.length} rejected ·{" "}
        {errors.length} errors
      </summary>

      <p className="mt-3 text-xs text-gray-500">
        Results update by polling (no live stream); single local profile.
      </p>

      <section className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Failed jobs ({failures.length})
        </h4>
        {failures.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">None</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {failures.map((f) => (
              <li key={f.job_id} className="text-sm text-gray-700">
                <span className="font-medium text-gray-900">{f.title}</span>
                {f.company ? ` · ${f.company}` : ""}
                {" — "}
                <span className="text-gray-500">
                  stage: {f.failure_stage ?? "unknown"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Rejected jobs ({rejected.length})
        </h4>
        {rejected.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">None</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {rejected.map((r) => (
              <li key={r.job_id} className="text-sm text-gray-700">
                <span className="font-medium text-gray-900">{r.title}</span>
                {r.company ? ` · ${r.company}` : ""}
                {" — "}
                <span className="text-gray-500">
                  {r.judgment?.decision ?? "—"}
                  {r.judgment
                    ? ` (${Math.round(r.judgment.confidence * 100)}%)`
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Errors ({errors.length})
        </h4>
        {errors.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">None</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {errors.map((e, i) => (
              <li key={i} className="text-sm text-gray-700">
                <span className="font-mono text-xs text-gray-500">
                  {e.stage}/{e.code}
                </span>
                {" — "}
                {e.message}
              </li>
            ))}
          </ul>
        )}
      </section>
    </details>
  );
}
