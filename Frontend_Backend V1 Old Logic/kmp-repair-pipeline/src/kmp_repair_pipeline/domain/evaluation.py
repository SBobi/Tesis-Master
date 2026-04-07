"""Evaluation domain types — metrics for the thesis evaluation framework."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    """Legacy precision/recall/F1 evaluation result (ported from prototype)."""
    scenario: str = ""
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    true_positives: list[str] = Field(default_factory=list)
    false_positives: list[str] = Field(default_factory=list)
    false_negatives: list[str] = Field(default_factory=list)
    screen_precision: float = 0.0
    screen_recall: float = 0.0
    screen_f1: float = 0.0
    screen_tp: list[str] = Field(default_factory=list)
    screen_fp: list[str] = Field(default_factory=list)
    screen_fn: list[str] = Field(default_factory=list)


class RepairMetrics(BaseModel):
    """Thesis repair metrics per case or aggregate.

    BSR  = Build Success Rate
    CTSR = Cross-Target Success Rate
    FFSR = File Fix Success Rate
    EFR  = Error Fix Rate
    """
    case_id: str = ""
    repair_mode: str = ""          # full_thesis | raw_error | context_rich | iterative_agentic
    bsr: float = 0.0               # fraction of cases: post-repair build succeeds
    ctsr: float = 0.0              # fraction of cases: ALL targets succeed
    ffsr: float = 0.0              # fraction of broken files correctly repaired
    efr: float = 0.0               # fraction of individual errors resolved
    hit_at_1: float = 0.0          # localized files overlap @ rank 1
    hit_at_3: float = 0.0          # localized files overlap @ rank 3
    hit_at_5: float = 0.0          # localized files overlap @ rank 5
    source_set_accuracy: float = 0.0  # correct shared/platform/build attribution
    total_cases: int = 0
    successful_cases: int = 0
    partial_cases: int = 0
    failed_cases: int = 0
    not_run_cases: int = 0
