import type { EvaluatedJob } from "@/lib/types";
import JobCard from "./JobCard";

// Recruitment-agency listings are filtered at the relevance screen (only present here when
// the run opted in via "Include recruitment agencies"). Both the agency decision AND the
// deprioritize-to-the-tail ordering are server-side (graph/nodes.py screen_jobs) — this
// component renders the order it is given rather than re-deriving it, so there is one
// implementation of the rule instead of two that can drift apart.
export default function QualifiedJobs({ jobs, runId }: { jobs: EvaluatedJob[]; runId: string }) {
  const agencyCount = jobs.filter((j) => j.is_recruitment_agency === true).length;

  return (
    <section>
      <h2 className="mb-3 text-lg font-semibold text-gray-900">
        Qualified jobs ({jobs.length})
      </h2>

      {agencyCount > 0 && (
        <p className="mb-3 text-sm text-gray-500">
          Includes {agencyCount} recruitment agency{" "}
          {agencyCount === 1 ? "listing" : "listings"}, shown last, because you opted
          in for this search.
        </p>
      )}

      {jobs.length === 0 ? (
        <p className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600 shadow-sm">
          Nothing qualified for this resume. The audit panel below shows everything
          we looked at and why each one was ruled out.
        </p>
      ) : (
        <div className="space-y-4">
          {jobs.map((job) => (
            <JobCard key={job.job_id} job={job} runId={runId} />
          ))}
        </div>
      )}
    </section>
  );
}
