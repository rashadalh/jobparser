import type { EvaluatedJob } from "@/lib/types";
import JobCard from "./JobCard";

// Recruitment-agency listings are filtered at the relevance screen (only present here when
// the run opted in via "Include recruitment agencies"). When present, they're deprioritized
// to the end of the list and badged; the agency relevance decision lives server-side.
export default function QualifiedJobs({ jobs }: { jobs: EvaluatedJob[] }) {
  const isAgency = (jb: EvaluatedJob) => jb.is_recruitment_agency === true;
  const ordered = [...jobs.filter((j) => !isAgency(j)), ...jobs.filter(isAgency)];
  const agencyCount = jobs.filter(isAgency).length;

  return (
    <section>
      <h2 className="mb-3 text-lg font-semibold text-gray-900">
        Qualified jobs ({jobs.length})
      </h2>

      {agencyCount > 0 && (
        <p className="mb-3 text-sm text-gray-500">
          Includes {agencyCount} recruitment-agency{" "}
          {agencyCount === 1 ? "listing" : "listings"} (shown last) — you opted in for
          this search.
        </p>
      )}

      {jobs.length === 0 ? (
        <p className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600 shadow-sm">
          No qualifying jobs found for this resume. See the audit panel below for what
          was evaluated.
        </p>
      ) : (
        <div className="space-y-4">
          {ordered.map((job) => (
            <JobCard key={job.job_id} job={job} />
          ))}
        </div>
      )}
    </section>
  );
}
