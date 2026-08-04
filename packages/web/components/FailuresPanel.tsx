import type {
  EvaluatedJob,
  ErrorRecord,
  JobRequirements,
  ScreenedJob,
} from "@/lib/types";
import Pills from "@/components/Pills";
import FeedbackForm from "@/components/FeedbackForm";

// Plain-language explanation of each pipeline stage a job can fail at.
const STAGE_EXPLAINER: Record<string, string> = {
  resolve: "The job's redirect could not be resolved to a real employer URL.",
  fetch: "The job page could not be fetched (blocked, timed out, or unreachable).",
  extract: "No job description could be extracted from the page.",
  quality: "The extracted description was too short or too noisy to evaluate.",
  parse: "The description could not be parsed into structured requirements.",
  judge: "The fit judgment failed schema validation.",
};

function JobLink({ url }: { url: string | null }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-sm font-medium text-blue-600 hover:underline"
    >
      View job posting →
    </a>
  );
}

function ParsedRequirements({ req }: { req: JobRequirements | null }) {
  if (!req) return null;
  return (
    <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3">
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        What the model read from the job description
      </span>
      <Pills label="Required skills" items={req.required_skills} />
      <Pills label="Preferred skills" items={req.preferred_skills} />
      <Pills label="Dealbreakers" items={req.dealbreakers} />
      <p className="mt-2 text-xs text-gray-600">
        Min experience:{" "}
        {req.min_years_experience === null
          ? "not stated"
          : `${req.min_years_experience} yrs`}
        {" · "}Education required: {req.education_required ? "yes" : "no"}
        {req.remote_allowed !== null
          ? ` · Remote allowed: ${req.remote_allowed ? "yes" : "no"}`
          : ""}
      </p>
    </div>
  );
}

/** One bucket of jobs that never reached evaluation, with why. */
function ScreenedSection({
  title,
  explainer,
  jobs,
}: {
  title: string;
  explainer: string;
  jobs: ScreenedJob[];
  /** Renders nothing when empty EXCEPT where a caller wants the "None" state — see
   *  `alwaysShow` at the call site for off-field, the one bucket that reports zero. */
}) {
  return (
    <section className="mt-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {title} ({jobs.length})
      </h4>
      <p className="mt-0.5 text-xs text-gray-400">{explainer}</p>
      {jobs.length === 0 ? (
        <p className="mt-1 text-sm text-gray-400">None</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {jobs.map((s) => (
            <li key={s.job_id} className="text-sm text-gray-700">
              <span className="font-medium text-gray-900">{s.title}</span>
              {s.company ? ` · ${s.company}` : ""}
              {s.location ? (
                <span className="text-gray-500"> · {s.location}</span>
              ) : (
                ""
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** A rejected job — the judge DID evaluate it; show its full reasoning, and let the
 *  candidate push back on it. A rejection is a judgment call over an imperfect resume
 *  summary, so it can be wrong in the candidate's favour just as a match can be wrong
 *  against them; `runId` is threaded down purely to make that possible. */
function RejectedItem({ job, runId }: { job: EvaluatedJob; runId: string }) {
  const j = job.judgment;
  return (
    <details
      data-testid="audit-item"
      className="rounded-lg border border-gray-100 px-3 py-2"
    >
      <summary className="cursor-pointer text-sm text-gray-700">
        <span className="font-medium text-gray-900">{job.title}</span>
        {job.company ? ` · ${job.company}` : ""}
        {" — "}
        <span className="text-gray-500">
          {j?.decision ?? "—"}
          {j ? ` (${Math.round(j.confidence * 100)}% confidence)` : ""}
        </span>
      </summary>

      <div className="mt-3 space-y-1 border-l-2 border-gray-200 pl-3">
        {j ? (
          <>
            <div>
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                Why this was {j.decision}
              </span>
              <p
                data-testid="reasoning"
                className="mt-1 text-sm italic text-gray-700"
              >
                “{j.rationale}”
              </p>
            </div>

            <Pills
              label="Missing required"
              items={j.missing_hard_requirements}
            />
            <Pills label="Failed dealbreakers" items={j.failed_dealbreakers} />

            {j.met_requirements.length > 0 && (
              <div className="mt-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Requirements it did meet
                </span>
                <ul className="mt-1 space-y-1">
                  {j.met_requirements.map((m, i) => (
                    <li key={i} className="text-sm text-gray-700">
                      <span className="font-medium">{m.requirement}</span>
                      <span className="mt-0.5 block border-l-2 border-gray-200 pl-2 text-xs italic text-gray-500">
                        “{m.evidence_quote}”
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-gray-500">No judgment recorded.</p>
        )}

        <ParsedRequirements req={job.requirements} />
        <div className="mt-3">
          <JobLink url={job.final_url} />
        </div>

        <div className="mt-3 border-t border-gray-100 pt-3">
          <FeedbackForm
            runId={runId}
            jobId={job.job_id}
            openLabel="Disagree with this?"
            prompt="Why do you think you're a fit for this job?"
            placeholder="e.g. I led Kubernetes migrations at Acme — my resume only lists Docker"
            doneText="Noted — your next run for this resume will take this into account."
          />
        </div>
      </div>
    </details>
  );
}

/** A failed job — it broke at a pipeline stage before/at evaluation. */
function FailedItem({
  job,
  error,
}: {
  job: EvaluatedJob;
  error: ErrorRecord | undefined;
}) {
  const stage = job.failure_stage ?? "unknown";
  return (
    <details
      data-testid="audit-item"
      className="rounded-lg border border-gray-100 px-3 py-2"
    >
      <summary className="cursor-pointer text-sm text-gray-700">
        <span className="font-medium text-gray-900">{job.title}</span>
        {job.company ? ` · ${job.company}` : ""}
        {" — "}
        <span className="text-gray-500">failed at {stage}</span>
      </summary>

      <div
        data-testid="reasoning"
        className="mt-3 space-y-2 border-l-2 border-gray-200 pl-3"
      >
        <p className="text-sm text-gray-700">{STAGE_EXPLAINER[stage] ?? ""}</p>
        {error && (
          <p className="text-sm text-gray-700">
            <span className="font-mono text-xs text-gray-500">{error.code}</span>
            {error.message && error.message !== error.code
              ? ` — ${error.message}`
              : ""}
          </p>
        )}
        {job.jd_char_len !== null && (
          <p className="text-xs text-gray-500">
            Extracted description length: {job.jd_char_len} characters
          </p>
        )}
        <JobLink url={job.final_url} />
      </div>
    </details>
  );
}

export default function FailuresPanel({
  failures,
  rejected,
  errors,
  screened,
  runId,
}: {
  failures: EvaluatedJob[];
  rejected: EvaluatedJob[];
  errors: ErrorRecord[];
  screened: ScreenedJob[];
  runId: string;
}) {
  // Correlate a failed job with its per-job ErrorRecord (code + message) by job_id.
  const errByJob = new Map<string, ErrorRecord>();
  for (const e of errors) if (e.job_id) errByJob.set(e.job_id, e);

  // Split the screened-out jobs by WHY they skipped evaluation. Legacy runs (no
  // `reason`) are treated as off-field, matching the prior single-bucket behavior.
  const overCap = screened.filter((s) => s.reason === "over_cap");
  const agency = screened.filter((s) => s.reason === "agency");
  const offField = screened.filter(
    (s) => s.reason !== "over_cap" && s.reason !== "agency",
  );

  return (
    <details
      data-testid="failures-panel"
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
    >
      <summary className="cursor-pointer text-sm font-medium text-gray-700">
        Audit — {failures.length} failed · {rejected.length} rejected ·{" "}
        {errors.length} errors · {screened.length} filtered
      </summary>

      <p className="mt-3 text-xs text-gray-500">
        Results update by polling (no live stream); single local profile. Click any
        row below to see the model&apos;s reasoning.
      </p>

      <section className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Rejected jobs ({rejected.length})
        </h4>
        {rejected.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">None</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {rejected.map((r) => (
              <li key={r.job_id}>
                <RejectedItem job={r} runId={runId} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Failed jobs ({failures.length})
        </h4>
        {failures.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">None</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {failures.map((f) => (
              <li key={f.job_id}>
                <FailedItem job={f} error={errByJob.get(f.job_id)} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* off-field always renders (its "None" is meaningful: nothing was dropped);
          the other two appear only when non-empty, as before. */}
      <ScreenedSection
        title="Filtered as off-field"
        explainer="Dropped before evaluation as not in your field (a keyword match in an unrelated industry/role), to keep the feed and cost focused."
        jobs={offField}
      />

      {overCap.length > 0 && (
        <ScreenedSection
          title="In-field, not evaluated"
          explainer="Relevant to your field but past this run's evaluation budget (the top matches were evaluated first). Re-run or narrow the search to reach these."
          jobs={overCap}
        />
      )}

      {agency.length > 0 && (
        <ScreenedSection
          title="Recruitment agencies, not evaluated"
          explainer="In-field but screened out as third-party recruiter/staffing listings. Check “Include recruitment agencies” in the search panel and re-run to evaluate these."
          jobs={agency}
        />
      )}

      <section className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          All errors ({errors.length})
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
                {e.message ? ` — ${e.message}` : ""}
                {e.job_id ? (
                  <span className="text-xs text-gray-400"> (job {e.job_id})</span>
                ) : (
                  <span className="text-xs text-gray-400"> (run-level)</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </details>
  );
}
