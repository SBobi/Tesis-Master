"""Phase 10 — Validate patch across KMP targets.

Orchestration:
  1. Rehydrate CaseBundle from DB
    2. Find patch attempt (specific ID, latest APPLIED, or latest available)
  3. Detect the build environment on the patched after-clone
  4. Run Gradle per runnable target on the patched workspace
  5. Persist ValidationRun rows + task/error rows
  6. Update patch_attempt.status → VALIDATED or REJECTED
  7. Advance case status → VALIDATED
  8. Return ValidationResult

The patched workspace is the after-clone already modified in place by Phase 9.
No re-cloning or patch reversal is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.evidence import (
    ErrorObservation as EvidenceError,
    TargetValidation,
    TaskOutcome,
    ValidationEvidence,
)
from ..case_bundle.serialization import from_db_case, to_db
from ..domain.validation import ValidationStatus
from ..runners.env_detector import detect
from ..runners.gradle_runner import GradleRunResult, run_tasks, tasks_for_target
from ..storage.artifact_store import ArtifactStore
from ..storage.repositories import (
    ErrorObservationRepo,
    ExecutionRunRepo,
    PatchAttemptRepo,
    RepairCaseRepo,
    RevisionRepo,
    TaskResultRepo,
    ValidationRunRepo,
)
from ..utils.log import get_logger

log = get_logger(__name__)


@dataclass
class ValidationResult:
    case_id: str
    patch_attempt_id: str
    patch_attempt_number: int
    repair_mode: str
    patch_status: str          # "VALIDATED" | "REJECTED"
    overall_status: str        # ValidationStatus value
    target_results: list[TargetValidation] = field(default_factory=list)


def validate(
    case_id: str,
    session: Session,
    artifact_base: Path | str = Path("data/artifacts"),
    patch_attempt_id: Optional[str] = None,
    targets: Optional[list[str]] = None,
    timeout_s: int = 600,
) -> ValidationResult:
    """Validate a patch attempt for *case_id*.

    Parameters
    ----------
    case_id:
        UUID of the repair case (must be in PATCH_ATTEMPTED status).
    session:
        Active SQLAlchemy session (caller controls commit).
    artifact_base:
        Root of the local artifact store.
    patch_attempt_id:
        UUID of a specific PatchAttempt row. If None, picks the most recent
        attempt whose status is ``"APPLIED"``; when none exists, falls back to
        the most recent attempt available.
    targets:
        Override the auto-detected target list.
    timeout_s:
        Per-Gradle-task timeout in seconds.
    """
    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    # ── Resolve the patch attempt row ──────────────────────────────────────
    attempt_repo = PatchAttemptRepo(session)
    if patch_attempt_id:
        attempt_row = attempt_repo.get_by_id(patch_attempt_id)
        if attempt_row is None:
            raise ValueError(f"PatchAttempt {patch_attempt_id} not found")
        if attempt_row.repair_case_id != case_id:
            raise ValueError(
                f"PatchAttempt {patch_attempt_id} belongs to a different case"
            )
    else:
        all_attempts = attempt_repo.list_for_case(case_id)
        applied = [a for a in all_attempts if a.status == "APPLIED"]
        if applied:
            attempt_row = applied[-1]   # most recent APPLIED
        elif all_attempts:
            attempt_row = all_attempts[-1]  # most recent overall
            log.warning(
                "Case %s has no APPLIED patch attempt; falling back to latest attempt #%d (%s)",
                case_id[:8],
                attempt_row.attempt_number,
                attempt_row.status,
            )
        else:
            raise ValueError(
                f"Case {case_id} has no patch attempts — run `repair` first"
            )

    log.info(
        "Case %s: validating attempt #%d (mode=%s id=%s)",
        case_id[:8], attempt_row.attempt_number, attempt_row.repair_mode, attempt_row.id[:8],
    )

    # ── Locate the patched workspace (after-clone, already patched in place) ─
    rev_repo = RevisionRepo(session)
    after_rev = rev_repo.get(case_id, "after")
    if after_rev is None or not after_rev.local_path:
        raise ValueError(
            f"Case {case_id}: after-clone not available — run `build-case` first"
        )
    repo_path = Path(after_rev.local_path)

    # ── Environment detection ───────────────────────────────────────────────
    env = detect(repo_path)
    effective_targets = targets or env.runnable_targets
    log.info(
        "Case %s: runnable=%s unavailable=%s",
        case_id[:8], effective_targets, list(env.unavailable_targets.keys()),
    )

    artifact_store = ArtifactStore(artifact_base, case_id)
    val_run_repo = ValidationRunRepo(session)
    run_repo = ExecutionRunRepo(session)
    task_repo = TaskResultRepo(session)
    error_repo = ErrorObservationRepo(session)

    target_results: list[TargetValidation] = []

    # ── Run each target ─────────────────────────────────────────────────────
    for target in effective_targets:
        task_outcomes, errors, exec_run_id = _run_target(
            case_id=case_id,
            target=target,
            repo_path=repo_path,
            attempt_number=attempt_row.attempt_number,
            repair_mode=attempt_row.repair_mode,
            artifact_store=artifact_store,
            run_repo=run_repo,
            task_repo=task_repo,
            error_repo=error_repo,
            timeout_s=timeout_s,
        )

        target_status = _aggregate_status(task_outcomes)

        val_run = val_run_repo.create(
            repair_case_id=case_id,
            patch_attempt_id=attempt_row.id,
            target=target,
            status=target_status,
            execution_run_id=exec_run_id,
        )
        # Record timing on the val_run
        val_run.ended_at = datetime.now(timezone.utc)
        session.flush()

        target_results.append(TargetValidation(
            target=target,
            status=ValidationStatus(target_status),
            patch_attempt_number=attempt_row.attempt_number,
            task_outcomes=task_outcomes,
            error_observations=errors,
        ))

    # ── Unavailable targets ──────────────────────────────────────────────────
    for target, reason in env.unavailable_targets.items():
        val_run_repo.create(
            repair_case_id=case_id,
            patch_attempt_id=attempt_row.id,
            target=target,
            status=ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE.value,
            unavailable_reason=reason,
        )
        target_results.append(TargetValidation(
            target=target,
            status=ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE,
            unavailable_reason=reason,
            patch_attempt_number=attempt_row.attempt_number,
        ))

    # ── Overall status and patch disposition ────────────────────────────────
    runnable_statuses = [
        r.status for r in target_results
        if r.status != ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE
    ]
    overall = _aggregate_status_values(runnable_statuses)
    patch_status = (
        "VALIDATED" if overall == ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value else "REJECTED"
    )

    # ── Persist ValidationEvidence to bundle ────────────────────────────────
    val_evidence = ValidationEvidence(
        target_results=target_results,
        repository_level_status=ValidationStatus(overall),
    )
    bundle.validation = val_evidence
    to_db(bundle, session)

    # ── Update patch_attempt status ─────────────────────────────────────────
    attempt_row.status = patch_status
    attempt_row.updated_at = datetime.now(timezone.utc)
    session.flush()

    # ── Advance case status ──────────────────────────────────────────────────
    case_row = RepairCaseRepo(session).get_by_id(case_id)
    RepairCaseRepo(session).set_status(case_row, "VALIDATED")
    bundle.meta.status = "VALIDATED"

    log.info(
        "Case %s validation complete: patch_status=%s overall=%s",
        case_id[:8], patch_status, overall,
    )

    return ValidationResult(
        case_id=case_id,
        patch_attempt_id=attempt_row.id,
        patch_attempt_number=attempt_row.attempt_number,
        repair_mode=attempt_row.repair_mode,
        patch_status=patch_status,
        overall_status=overall,
        target_results=target_results,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_target(
    case_id: str,
    target: str,
    repo_path: Path,
    attempt_number: int,
    repair_mode: str,
    artifact_store: ArtifactStore,
    run_repo: ExecutionRunRepo,
    task_repo: TaskResultRepo,
    error_repo: ErrorObservationRepo,
    timeout_s: int,
) -> tuple[list[TaskOutcome], list[EvidenceError], str]:
    """Run all Gradle tasks for one target; return (task_outcomes, errors, exec_run_id)."""
    revision_label = f"validation_{attempt_number:03d}_{repair_mode}"

    exec_run = run_repo.create(
        repair_case_id=case_id,
        revision_type=revision_label,
        profile="validation",
        env_metadata={"target": target, "attempt": attempt_number, "mode": repair_mode},
    )

    gradle_tasks = tasks_for_target(target)
    results: list[GradleRunResult] = run_tasks(
        repo_path=repo_path,
        tasks=gradle_tasks,
        timeout_s=timeout_s,
    )

    task_outcomes: list[TaskOutcome] = []
    all_errors: list[EvidenceError] = []

    for gr in results:
        stdout_path, stdout_sha, stderr_path, stderr_sha = artifact_store.write_task_output(
            revision_label, gr.task_name, gr.stdout, gr.stderr
        )

        task_row = task_repo.create(
            execution_run_id=exec_run.id,
            task_name=gr.task_name,
            exit_code=gr.exit_code,
            status=gr.status,
            duration_s=gr.duration_s,
            stdout_path=stdout_path,
            stdout_sha256=stdout_sha,
            stderr_path=stderr_path,
            stderr_sha256=stderr_sha,
        )

        for err in gr.error_observations:
            error_repo.create(
                task_result_id=task_row.id,
                error_type=err.error_type,
                file_path=err.file_path,
                line=err.line,
                column=err.column,
                message=err.message,
                raw_text=err.raw_text,
                parser=err.parser,
            )

        task_outcomes.append(TaskOutcome(
            task_name=gr.task_name,
            exit_code=gr.exit_code,
            status=ValidationStatus(gr.status),
            duration_s=gr.duration_s,
            stdout_path=stdout_path,
            stdout_sha256=stdout_sha,
            stderr_path=stderr_path,
            stderr_sha256=stderr_sha,
        ))
        all_errors.extend(
            EvidenceError(
                error_type=e.error_type,
                file_path=e.file_path,
                line=e.line,
                column=e.column,
                message=e.message,
                raw_text=e.raw_text,
                parser=e.parser,
            )
            for e in gr.error_observations
        )

    exec_run.ended_at = datetime.now(timezone.utc)
    return task_outcomes, all_errors, exec_run.id


def _aggregate_status(task_outcomes: list[TaskOutcome]) -> str:
    """Compute overall status from a list of TaskOutcome objects."""
    return _aggregate_status_values([t.status for t in task_outcomes])


def _aggregate_status_values(statuses: list) -> str:
    """Compute overall status from a list of ValidationStatus values or strings."""
    if not statuses:
        return ValidationStatus.NOT_RUN_YET.value
    # Normalise to enum
    norm = []
    for s in statuses:
        norm.append(ValidationStatus(s) if isinstance(s, str) else s)

    if all(s == ValidationStatus.SUCCESS_REPOSITORY_LEVEL for s in norm):
        return ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
    if any(s == ValidationStatus.FAILED_BUILD for s in norm):
        return ValidationStatus.FAILED_BUILD.value
    if any(s == ValidationStatus.FAILED_TESTS for s in norm):
        return ValidationStatus.FAILED_TESTS.value
    return ValidationStatus.INCONCLUSIVE.value
