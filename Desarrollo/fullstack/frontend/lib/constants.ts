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
