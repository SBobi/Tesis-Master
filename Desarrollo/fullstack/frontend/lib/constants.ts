export const CASE_STATES = [
  "CREATED",
  "SHADOW_BUILT",
  "EXECUTED",
  "LOCALIZED",
  "PATCH_ATTEMPTED",
  "VALIDATED",
  "EXPLAINED",
  "EVALUATED",
  "NO_ERRORS_TO_FIX",
  "FAILED",
] as const;

export const PIPELINE_STAGES = [
  "ingest",
  "build-case",
  "run-before-after",
  "analyze-case",
  "localize",
  "repair",
  "validate",
  "explain",
  "metrics",
  "report",
] as const;

export const RUNNABLE_STAGES = PIPELINE_STAGES.slice(1);

export const REPAIR_MODES = ["full_thesis", "raw_error", "context_rich", "iterative_agentic"] as const;

export const BASELINE_MODES = ["raw_error", "context_rich", "iterative_agentic"] as const;

export const TARGETS = ["shared", "android", "ios"] as const;

export const PROVIDERS = ["anthropic", "vertex"] as const;

export const PATCH_STRATEGIES = ["single_diff", "chain_by_file"] as const;

export const REPORT_FORMATS = ["csv", "json", "markdown", "all"] as const;
