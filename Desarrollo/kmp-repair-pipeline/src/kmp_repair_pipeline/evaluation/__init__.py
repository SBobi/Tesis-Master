"""Phase 12 — Evaluation framework: thesis metrics + baseline comparison."""

from .evaluator import EvaluationRunResult, evaluate
from .metrics import (
    CaseMetrics,
    compute_bsr,
    compute_ctsr,
    compute_efr,
    compute_ffsr,
    compute_hit_at_k,
    compute_metrics,
    compute_source_set_accuracy,
)

__all__ = [
    "evaluate",
    "EvaluationRunResult",
    "compute_metrics",
    "CaseMetrics",
    "compute_bsr",
    "compute_ctsr",
    "compute_ffsr",
    "compute_efr",
    "compute_hit_at_k",
    "compute_source_set_accuracy",
]
