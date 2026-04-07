export function stageLabel(stage: string): string {
  switch (stage) {
    case "ingest":
      return "Ingest";
    case "build-case":
      return "Build Case";
    case "run-before-after":
      return "Run Before/After";
    case "analyze-case":
      return "Analyze Case";
    case "localize":
      return "Localize";
    case "repair":
      return "Repair";
    case "validate":
      return "Validate";
    case "explain":
      return "Explain";
    case "metrics":
      return "Metrics";
    case "report":
      return "Report";
    default:
      return stage;
  }
}

export function stageStatusTone(status: string): "neutral" | "success" | "warning" | "danger" | "running" {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "danger";
  if (status === "RUNNING") return "running";
  if (status === "NOT_RUN_ENVIRONMENT_UNAVAILABLE") return "warning";
  return "neutral";
}

export function caseStatusLabel(status: string): string {
  switch (status) {
    case "CREATED":
      return "Created";
    case "SHADOW_BUILT":
      return "Shadow Built";
    case "EXECUTED":
      return "Executed";
    case "LOCALIZED":
      return "Localized";
    case "PATCH_ATTEMPTED":
      return "Patch Attempted";
    case "VALIDATED":
      return "Validated";
    case "EXPLAINED":
      return "Explained";
    case "EVALUATED":
      return "Evaluated";
    case "NO_ERRORS_TO_FIX":
      return "No Errors To Fix";
    case "FAILED":
      return "Failed";
    default:
      return status;
  }
}

export function validationLabel(status: string): string {
  switch (status) {
    case "SUCCESS_REPOSITORY_LEVEL":
      return "Success";
    case "PARTIAL_SUCCESS":
      return "Partial";
    case "FAILED_BUILD":
      return "Build Failed";
    case "FAILED_TESTS":
      return "Tests Failed";
    case "NOT_RUN_ENVIRONMENT_UNAVAILABLE":
      return "Unavailable";
    case "NOT_RUN_YET":
      return "Not Run";
    case "INCONCLUSIVE":
      return "Inconclusive";
    default:
      return status;
  }
}
