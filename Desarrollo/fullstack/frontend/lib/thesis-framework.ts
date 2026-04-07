import { REPAIR_MODES } from "@/lib/constants";

export type RepairModeKey = (typeof REPAIR_MODES)[number];

type ThesisRepairModeDetail = {
  contextGivenToRepairAgent: string;
  retryBudget: number;
  notes: string;
};

export const THESIS_PRIMARY_MODE: RepairModeKey = "full_thesis";

export const THESIS_REPAIR_MODE_ORDER: RepairModeKey[] = [
  "raw_error",
  "context_rich",
  "iterative_agentic",
  "full_thesis",
];

export const THESIS_REPAIR_MODE_LABELS: Record<RepairModeKey, string> = {
  raw_error: "RAW_ERROR",
  context_rich: "CONTEXT_RICH",
  iterative_agentic: "ITERATIVE_AGENTIC",
  full_thesis: "FULL_THESIS",
};

// Source wording mirrors the thesis-facing baseline table in kmp-repair-pipeline/README.md.
export const THESIS_REPAIR_MODE_DETAILS: Record<RepairModeKey, ThesisRepairModeDetail> = {
  raw_error: {
    contextGivenToRepairAgent: "Dep diff + raw compiler errors only",
    retryBudget: 2,
    notes: "Minimal baseline; tests if errors alone suffice",
  },
  context_rich: {
    contextGivenToRepairAgent: "+ localized files + source-set info + version catalog",
    retryBudget: 3,
    notes: "Adds file content + build evidence",
  },
  iterative_agentic: {
    contextGivenToRepairAgent: "Same as context_rich + previous-attempt feedback",
    retryBudget: 4,
    notes: "Retry loop with rejection guidance",
  },
  full_thesis: {
    contextGivenToRepairAgent: "Full Case Bundle evidence + all previous attempts",
    retryBudget: 5,
    notes: "Maximum context; thesis primary baseline",
  },
};

// Source wording mirrors the thesis stage framing in docs/architecture-memo.md.
export const THESIS_CORE_PRINCIPLE =
  "The system is an evidence-and-decision pipeline, not a generic coding agent.";

export const THESIS_FIVE_PIPELINE_STAGES = [
  "Update ingestion and typing",
  "Before/after execution and evidence capture",
  "Hybrid impact localization",
  "Patch synthesis",
  "Multi-target validation and explanation",
] as const;
