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
