import type { EvaluatedJob } from "@/lib/types";

export default function JobCard({ job }: { job: EvaluatedJob }) {
  const j = job.judgment;
  const confidencePct = j ? Math.round(j.confidence * 100) : null;
  const isAgency = job.is_recruitment_agency === true;

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
    </article>
  );
}
