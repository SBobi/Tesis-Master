"""Phase 12 — Compute and persist thesis evaluation metrics for a repair case.

Orchestration:
  1. Rehydrate CaseBundle from DB (execution errors, localization, validation)
  2. Load validation-run error observations from DB (remaining errors)
  3. For each repair mode that has at least one patch attempt:
     a. Compute CaseMetrics
     b. Upsert EvaluationMetric row
  4. Advance case status → EVALUATED
  5. Return list[CaseMetrics]

Ground truth is optional. When provided it must be a dict with:
  - "changed_files": list[str]   — for Hit@k
  - "source_sets":   dict[str,str] — file_path → source_set, for attribution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.evidence import ErrorObservation
from ..case_bundle.serialization import from_db_case
from ..storage.repositories import (
    ErrorObservationRepo,
    EvaluationMetricRepo,
    PatchAttemptRepo,
    RepairCaseRepo,
    TaskResultRepo,
    ValidationRunRepo,
)
from ..utils.log import get_logger
from .metrics import CaseMetrics, compute_metrics

log = get_logger(__name__)


@dataclass
class EvaluationRunResult:
    case_id: str
    metrics: list[CaseMetrics] = field(default_factory=list)


def evaluate(
    case_id: str,
    session: Session,
    ground_truth: Optional[dict] = None,
) -> EvaluationRunResult:
    """Compute and persist all thesis metrics for *case_id*.

    Parameters
    ----------
    case_id:
        UUID of the repair case.
    session:
        Active SQLAlchemy session (caller controls commit).
    ground_truth:
        Optional dict with ``changed_files`` and ``source_sets`` keys.
    """
    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    gt_files: list[str] = (ground_truth or {}).get("changed_files", [])
    gt_source_sets: dict[str, str] = (ground_truth or {}).get("source_sets", {})

    # ── Original errors (after revision, pre-patch) ──────────────────────
    original_errors: list[ErrorObservation] = []
    if bundle.execution and bundle.execution.after:
        original_errors = list(bundle.execution.after.error_observations)

    # ── Localization candidates ──────────────────────────────────────────
    localization_candidates: list[dict] = []
    if bundle.repair and bundle.repair.localization:
        localization_candidates = [
            c.model_dump()
            for c in bundle.repair.localization.candidates
        ]

    # ── Find all repair modes with patch attempts ────────────────────────
    attempt_repo = PatchAttemptRepo(session)
    all_attempts = attempt_repo.list_for_case(case_id)
    if not all_attempts:
        log.warning("Case %s has no patch attempts — nothing to evaluate", case_id[:8])
        return EvaluationRunResult(case_id=case_id)

    # Group by repair_mode; pick the last attempt per mode
    by_mode: dict[str, object] = {}
    for a in all_attempts:
        by_mode[a.repair_mode] = a   # later attempt overwrites earlier

    metric_repo = EvaluationMetricRepo(session)
    results: list[CaseMetrics] = []

    for repair_mode, attempt_row in by_mode.items():
        # Remaining errors = errors from validation runs for this attempt
        remaining_errors = _load_remaining_errors(
            attempt_row.id, session
        )

        # Validation evidence scoped to this attempt's ValidationRun rows
        validation = bundle.validation  # already scoped to last attempt in bundle

        m = compute_metrics(
            case_id=case_id,
            repair_mode=repair_mode,
            validation=validation,
            original_errors=original_errors,
            remaining_errors=remaining_errors,
            localization_candidates=localization_candidates,
            ground_truth_files=gt_files,
            ground_truth_source_sets=gt_source_sets,
        )

        metric_repo.upsert(
            repair_case_id=case_id,
            repair_mode=repair_mode,
            bsr=m.bsr,
            ctsr=m.ctsr,
            ffsr=m.ffsr,
            efr=m.efr,
            hit_at_1=m.hit_at_1,
            hit_at_3=m.hit_at_3,
            hit_at_5=m.hit_at_5,
            source_set_accuracy=m.source_set_accuracy,
        )
        results.append(m)
        log.info(
            "Case %s mode=%s BSR=%.1f CTSR=%.1f FFSR=%.1f EFR=%s Hit@1=%s",
            case_id[:8], repair_mode, m.bsr, m.ctsr, m.ffsr,
            f"{m.efr:.3f}" if m.efr is not None else "N/A",
            f"{m.hit_at_1:.1f}" if m.hit_at_1 is not None else "N/A",
        )

    # ── Advance case status ──────────────────────────────────────────────
    case_row = RepairCaseRepo(session).get_by_id(case_id)
    RepairCaseRepo(session).set_status(case_row, "EVALUATED")

    return EvaluationRunResult(case_id=case_id, metrics=results)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_remaining_errors(
    patch_attempt_id: str,
    session: Session,
) -> list[ErrorObservation]:
    """Load ErrorObservation rows linked to ValidationRun task results."""
    val_run_repo = ValidationRunRepo(session)
    task_repo = TaskResultRepo(session)
    error_repo = ErrorObservationRepo(session)

    val_runs = val_run_repo.list_for_patch(patch_attempt_id)
    errors: list[ErrorObservation] = []

    for vr in val_runs:
        if vr.execution_run_id is None:
            continue
        # Find task results for this execution_run
        from sqlalchemy import select
        from ..storage.models import TaskResult
        stmt = select(TaskResult).where(TaskResult.execution_run_id == vr.execution_run_id)
        task_rows = list(session.scalars(stmt).all())
        for tr in task_rows:
            for err_row in error_repo.list_for_task(tr.id):
                errors.append(ErrorObservation(
                    error_type=err_row.error_type,
                    file_path=err_row.file_path,
                    line=err_row.line,
                    column=err_row.column,
                    message=err_row.message,
                    raw_text=err_row.raw_text or "",
                    parser=err_row.parser or "",
                ))

    return errors
