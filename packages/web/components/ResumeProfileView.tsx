import type { ResumeProfile } from "@/lib/types";
import Pills from "@/components/Pills";

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
        This is what the matcher works from. If something here looks wrong or
        missing, that explains a bad match: it only ever sees what&apos;s on this page.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Scalar label="Seniority" value={profile.seniority} />
        <Scalar
          label="Total years of experience"
          value={`${profile.total_years_experience}`}
        />
        <Pills
          label="Education"
          items={profile.education}
          testid="profile-education"
          emptyText="none extracted"
        />
        <Pills
          label="Target roles"
          items={profile.roles}
          emptyText="none extracted"
        />
        <div className="sm:col-span-2">
          <Pills label="Skills" items={profile.skills} emptyText="none extracted" />
        </div>
        <Pills
          label="Domains"
          items={profile.domains}
          emptyText="none extracted"
        />
        <Pills
          label="Work authorization"
          items={profile.work_authorization}
          emptyText="none extracted"
        />
        <Pills
          label="Preferred locations"
          items={profile.locations}
          emptyText="none extracted"
        />
        <Scalar label="Remote preference" value={profile.remote_preference} />
        <div className="sm:col-span-2">
          <Pills
            label="Employment types"
            items={profile.employment_types}
            emptyText="none extracted"
          />
        </div>
      </div>

      <div className="mt-4">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Work history ({profile.total_years_experience} yrs total)
        </span>
        <p className="mt-0.5 text-xs text-gray-400">
          We work out the total from these dated roles rather than asking the model.
          Overlapping roles count once, and gaps between jobs don&apos;t count.
        </p>
        {profile.work_periods.length === 0 ? (
          <p className="mt-1 text-sm text-gray-400">none extracted</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {profile.work_periods.map((w, i) => (
              <li key={i} className="text-sm text-gray-700">
                <span className="font-medium">{w.title}</span>
                {w.organization ? ` · ${w.organization}` : ""}
                <span className="text-gray-500">
                  {" · "}{w.start_year} to {w.end_year ?? "present"}
                </span>
              </li>
            ))}
          </ul>
        )}
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
