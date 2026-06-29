import type { ResumeProfile } from "@/lib/types";

function Pills({
  label,
  values,
  testid,
}: {
  label: string;
  values: string[];
  testid?: string;
}) {
  return (
    <div data-testid={testid}>
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </span>
      {values.length === 0 ? (
        <p className="mt-1 text-sm text-gray-400">none extracted</p>
      ) : (
        <ul className="mt-1 flex flex-wrap gap-1">
          {values.map((v, i) => (
            <li
              key={i}
              className="rounded bg-gray-100 px-2 py-0.5 text-sm text-gray-700"
            >
              {v}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Scalar({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </span>
      <p className="mt-1 text-sm text-gray-800">{value}</p>
    </div>
  );
}

export default function ResumeProfileView({
  profile,
}: {
  profile: ResumeProfile;
}) {
  return (
    <details
      data-testid="profile-panel"
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
    >
      <summary className="cursor-pointer text-sm font-medium text-gray-700">
        What we extracted from your resume
      </summary>
      <p className="mt-2 text-xs text-gray-500">
        This is the structured profile the matcher reasons over. If something looks
        wrong or missing, that&apos;s why a job may have been mis-judged — the model
        only sees what&apos;s captured here.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Scalar label="Seniority" value={profile.seniority} />
        <Scalar
          label="Total years of experience"
          value={`${profile.total_years_experience}`}
        />
        <Pills
          label="Education"
          values={profile.education}
          testid="profile-education"
        />
        <Pills label="Target roles" values={profile.roles} />
        <div className="sm:col-span-2">
          <Pills label="Skills" values={profile.skills} />
        </div>
        <Pills label="Domains" values={profile.domains} />
        <Pills label="Work authorization" values={profile.work_authorization} />
        <Pills label="Preferred locations" values={profile.locations} />
        <Scalar label="Remote preference" value={profile.remote_preference} />
        <div className="sm:col-span-2">
          <Pills label="Employment types" values={profile.employment_types} />
        </div>
      </div>

      <div className="mt-4">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Evidence ({profile.evidence.length})
        </span>
        {profile.evidence.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">none</p>
        ) : (
          <ul className="mt-1 space-y-2">
            {profile.evidence.map((e, i) => (
              <li key={i} className="text-sm text-gray-700">
                <span className="font-medium">{e.claim}</span>
                <span className="mt-0.5 block border-l-2 border-gray-200 pl-2 text-xs italic text-gray-500">
                  &ldquo;{e.source_quote}&rdquo;
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <details data-testid="profile-raw-json" className="mt-4">
        <summary className="cursor-pointer text-xs font-medium text-gray-500">
          View raw JSON
        </summary>
        <pre className="mt-2 overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs text-gray-700">
          {JSON.stringify(profile, null, 2)}
        </pre>
      </details>
    </details>
  );
}
