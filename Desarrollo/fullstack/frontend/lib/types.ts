export type CaseStatus =
  | "CREATED"
  | "SHADOW_BUILT"
  | "EXECUTED"
  | "LOCALIZED"
  | "PATCH_ATTEMPTED"
  | "VALIDATED"
  | "EXPLAINED"
  | "EVALUATED"
  | "NO_ERRORS_TO_FIX"
  | "FAILED";

export type ValidationStatus =
  | "SUCCESS_REPOSITORY_LEVEL"
  | "PARTIAL_SUCCESS"
  | "FAILED_BUILD"
  | "FAILED_TESTS"
  | "NOT_RUN_ENVIRONMENT_UNAVAILABLE"
  | "INCONCLUSIVE"
  | "NOT_RUN_YET";

export interface Job {
  job_id: string;
  case_id: string;
  job_type: string;
  stage: string | null;
  start_from_stage: string | null;
  status: string;
  current_stage: string | null;
  command_preview: string | null;
  params?: Record<string, unknown> | null;
  effective_params: Record<string, unknown> | null;
  cancel_requested?: boolean;
  result_summary?: Record<string, unknown> | null;
  log_path: string | null;
  error_message: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface CaseSummary {
  case_id: string;
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  repository: {
    url: string;
    owner: string | null;
    name: string | null;
  };
  event: {
    pr_ref: string | null;
    pr_title: string | null;
    update_class: string;
  };
  latest_repair_mode: string | null;
  latest_patch_status: string | null;
  active_job: Job | null;
}

export interface TimelineStage {
  stage: string;
  status: "NOT_STARTED" | "RUNNING" | "COMPLETED" | "FAILED";
  duration_s: number | null;
  action: "run" | "retry";
  has_evidence: boolean;
}

export interface CaseDetail {
  case: {
    case_id: string;
    status: CaseStatus;
    artifact_dir: string | null;
    created_at: string;
    updated_at: string;
    repository: {
      url: string;
      owner: string | null;
      name: string | null;
    };
    event: {
      pr_ref: string | null;
      pr_title: string | null;
      update_class: string;
      raw_diff: string | null;
    };
  };
  timeline: TimelineStage[];
  evidence: {
    update_evidence: {
      changes: Array<{
        dependency_group: string;
        version_key: string | null;
        before: string;
        after: string;
      }>;
      raw_diff: string | null;
    };
    execution_before_after: Array<{
      run_id: string;
      revision_type: string;
      status: ValidationStatus;
      profile: string;
      started_at: string | null;
      ended_at: string | null;
      duration_s: number | null;
      env_metadata: Record<string, unknown> | null;
      tasks: Array<{
        task_id: string;
        task_name: string;
        status: ValidationStatus;
        exit_code: number | null;
        duration_s: number | null;
        stdout_path: string | null;
        stderr_path: string | null;
      }>;
    }>;
    structural_evidence: {
      source_entities_count: number;
      sample: Array<{
        file_path: string;
        source_set: string;
        fqcn: string | null;
        is_expect: boolean;
        is_actual: boolean;
      }>;
    };
    localization_ranking: Array<{
      rank: number;
      file_path: string;
      source_set: string | null;
      classification: string;
      score: number;
      score_breakdown: Record<string, unknown> | null;
    }>;
    patch_attempts: Array<{
      id: string;
      attempt_number: number;
      repair_mode: string;
      status: string;
      diff_path: string | null;
      diff_preview: string | null;
      touched_files: string[] | null;
      retry_reason: string | null;
      created_at: string;
    }>;
    validation_by_target: Array<{
      id: string;
      patch_attempt_id: string;
      target: string;
      status: ValidationStatus;
      unavailable_reason: string | null;
      started_at: string | null;
      ended_at: string | null;
      duration_s: number | null;
      execution_run_id: string | null;
    }>;
    explanations: Array<{
      id: string;
      json_path: string | null;
      json_preview: string | null;
      markdown_path: string | null;
      markdown_preview: string | null;
      model_id: string | null;
      tokens_in: number | null;
      tokens_out: number | null;
      created_at: string;
    }>;
    agent_logs: Array<{
      id: string;
      agent_type: string;
      call_index: number;
      model_id: string | null;
      tokens_in: number | null;
      tokens_out: number | null;
      latency_s: number | null;
      prompt_path: string | null;
      response_path: string | null;
      error: string | null;
      created_at: string;
    }>;
    metrics: Array<{
      repair_mode: string;
      bsr: number | null;
      ctsr: number | null;
      ffsr: number | null;
      efr: number | null;
      hit_at_1: number | null;
      hit_at_3: number | null;
      hit_at_5: number | null;
      source_set_accuracy: number | null;
      extra?: Record<string, unknown> | null;
      updated_at: string;
    }>;
  };
  jobs: Job[];
  history: Array<{
    transition_id: string;
    case_id: string;
    pipeline_job_id: string | null;
    stage: string | null;
    from_status: string | null;
    to_status: string | null;
    transition_type: string;
    message: string | null;
    metadata: Record<string, unknown> | null;
    created_at: string;
  }>;
}

export interface ReportsComparisonRow {
  repair_mode: string;
  cases: number;
  bsr: number | null;
  ctsr: number | null;
  ffsr: number | null;
  efr: number | null;
  hit_at_1: number | null;
  hit_at_3: number | null;
  hit_at_5: number | null;
  source_set_accuracy: number | null;
}

export interface HealthStatus {
  ok: boolean;
  service: string;
  time: string;
}

export interface EnvironmentChecks {
  api_database: boolean;
  python_version: string;
  python_ok: boolean;
  git_available: boolean;
  java_available: boolean;
  android_sdk_available: boolean;
  llm_provider_available: boolean;
}

export interface EnvironmentPaths {
  java_home: string;
  android_home: string;
  android_sdk_root: string;
  kmp_database_url: string;
  kmp_artifact_base: string;
  kmp_report_output_dir: string;
  google_application_credentials: string;
}

export interface EnvironmentLlm {
  provider: string;
  model: string;
  fake: boolean;
  vertex_project: string;
  vertex_location: string;
}

export interface EnvironmentDefaults {
  run_before_after_timeout_s: number;
  validate_timeout_s: number;
  localize_top_k: number;
  repair_top_k: number;
  queue_default_timeout_s: number;
}

export interface EnvironmentSnapshot {
  generated_at: string;
  checks: EnvironmentChecks;
  paths: EnvironmentPaths;
  llm: EnvironmentLlm;
  defaults: EnvironmentDefaults;
}
