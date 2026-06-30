export type FitDecision = "qualified" | "not_qualified" | "uncertain";
export type JobStatus = "qualified" | "not_qualified" | "uncertain" | "failed";

export interface MetRequirement { requirement: string; evidence_quote: string; }
export interface FitJudgment {
  decision: FitDecision; confidence: number;
  met_requirements: MetRequirement[]; missing_hard_requirements: string[];
  failed_dealbreakers: string[]; rationale: string;
}
export interface JobRequirements {
  required_skills: string[]; preferred_skills: string[];
  min_years_experience: number | null; education: string[];
  education_required: boolean; location_constraints: string[];
  remote_allowed: boolean | null; responsibilities: string[];
  dealbreakers: string[]; employment_type: string | null;
}
export interface EvaluatedJob {
  job_id: string; title: string; company: string; location: string;
  final_url: string | null; source: Record<string, unknown>;
  jd_char_len: number | null; requirements: JobRequirements | null;
  judgment: FitJudgment | null; status: JobStatus; failure_stage: string | null;
}
export interface ErrorRecord {
  job_id: string | null; stage: string; code: string;
  message: string; detail: Record<string, unknown> | null;
}
export interface ResumeEvidence { claim: string; source_quote: string; }
export interface WorkPeriod {
  title: string; organization: string;
  start_year: number; end_year: number | null;
}
export interface ResumeProfile {
  roles: string[]; skills: string[]; seniority: string;
  total_years_experience: number; work_periods: WorkPeriod[];
  education: string[]; domains: string[];
  work_authorization: string[]; locations: string[];
  remote_preference: string; employment_types: string[];
  evidence: ResumeEvidence[];
}
export interface StoredResumeProfile {
  id: string; cache_key: string; profile: ResumeProfile;
  model: string; created_at: string; updated_at: string;
}
export interface ProfileSummary {
  cache_key: string; id: string; created_at: string; updated_at: string;
  model: string; seniority: string; roles: string[]; education: string[];
  locations: string[];
}
export interface RunSummary {
  run_id: string; status: RunStatus; created_at: string; updated_at: string;
  qualified_count: number; rejected_count: number; failed_count: number;
  roles: string[]; error: string | null;
}
export interface ScreenedJob {
  job_id: string; title: string; company: string; location: string;
  // why it skipped evaluation: "off_field" (keyword collision, unrelated industry)
  // or "over_cap" (in-field but past the per-run evaluation budget). Older runs omit it.
  reason?: "off_field" | "over_cap";
}
export type RunStatus = "pending" | "running" | "completed" | "failed";
export interface RunRecord {
  run_id: string; user_id: string; status: RunStatus;
  created_at: string; updated_at: string; resume_cache_hit: boolean | null;
  resume_profile: ResumeProfile | null;
  qualified_jobs: EvaluatedJob[]; failures: EvaluatedJob[];
  rejected: EvaluatedJob[]; errors: ErrorRecord[];
  screened_out: ScreenedJob[]; error: string | null;
}
