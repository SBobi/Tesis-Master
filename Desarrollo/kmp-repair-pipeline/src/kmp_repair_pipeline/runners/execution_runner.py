"""Orchestrate before/after Gradle execution for one repair case.

Phase 6 top-level orchestrator:

  1. Rehydrate CaseBundle from DB
  2. Locate the before/after local paths from the revisions table
  3. Detect the build environment (env_detector)
  4. For each runnable target: run Gradle tasks (gradle_runner)
     - Persist ExecutionRun + TaskResult + ErrorObservation rows
     - Write stdout/stderr to ArtifactStore
  5. Build ExecutionEvidence and attach it to the CaseBundle
  6. Advance status → EXECUTED
  7. Return the updated bundle

Unavailable targets (iOS on Linux, Android without SDK) are recorded as
NOT_RUN_ENVIRONMENT_UNAVAILABLE — never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.evidence import (
    ErrorObservation as EvidenceError,
    ExecutionEvidence,
    RevisionExecution,
    TaskOutcome,
)
from ..case_bundle.serialization import from_db_case, to_db
from ..domain.validation import ValidationStatus
from ..storage.artifact_store import ArtifactStore
from ..storage.repositories import (
    ErrorObservationRepo,
    ExecutionRunRepo,
    RepairCaseRepo,
    RevisionRepo,
    TaskResultRepo,
)
from ..utils.log import get_logger
from .env_detector import EnvProfile, detect
from .gradle_runner import GradleRunResult, run_tasks, tasks_for_target

log = get_logger(__name__)

# The revision types we always attempt
_REVISIONS = ("before", "after")

# Profile labels for execution_runs.profile
_PROFILE_LINUX = "linux-fast"
_PROFILE_MACOS = "macos-full"


@dataclass
class ExecutionResult:
    bundle: CaseBundle
    env_profile: EnvProfile
    ran_revisions: list[str]   # which of "before"/"after" were executed
    total_errors: int


def run_before_after(
    case_id: str,
    session: Session,
    artifact_base: Path | str = Path("data/artifacts"),
    targets: Optional[list[str]] = None,
    timeout_s: int = 600,
) -> ExecutionResult:
    """Execute before and after revisions for `case_id`.

    Parameters
    ----------
    case_id:
        UUID of a repair case that has already been built (status SHADOW_BUILT).
    session:
        Active SQLAlchemy session (caller controls commit).
    artifact_base:
        Root of the artifact store.
    targets:
        Which KMP targets to run. ``None`` means auto-detect from EnvProfile.
    timeout_s:
        Per-task timeout in seconds.
    """
    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    if bundle.meta.status not in ("SHADOW_BUILT", "INGESTED", "CREATED", "EXECUTED"):
        log.warning(
            "Case %s at status %s — running execution anyway", case_id[:8], bundle.meta.status
        )

    rev_repo = RevisionRepo(session)
    artifact_store = ArtifactStore(artifact_base, case_id)

    # Detect environment using the after-clone (representative of the post-update state)
    after_rev = rev_repo.get(case_id, "after")
    if after_rev is None or not after_rev.local_path:
        raise ValueError(
            f"Case {case_id}: after revision not cloned yet — run `build-case` first"
        )

    after_path = Path(after_rev.local_path)
    env = detect(after_path)
    profile_name = _PROFILE_MACOS if env.is_macos else _PROFILE_LINUX

    # Determine which targets to run
    effective_targets = targets or env.runnable_targets
    log.info(
        "Case %s: runnable=%s unavailable=%s",
        case_id[:8], effective_targets, list(env.unavailable_targets.keys()),
    )

    all_task_outcomes: dict[str, list[TaskOutcome]] = {"before": [], "after": []}
    all_errors: dict[str, list[EvidenceError]] = {"before": [], "after": []}
    ran_revisions: list[str] = []

    # Execute each revision
    for revision_type in _REVISIONS:
        local_rev = rev_repo.get(case_id, revision_type)
        if local_rev is None or not local_rev.local_path:
            log.warning(
                "Case %s: %s revision not available — skipping",
                case_id[:8], revision_type,
            )
            continue

        repo_path = Path(local_rev.local_path)
        task_outcomes, errors = _run_revision(
            case_id=case_id,
            revision_type=revision_type,
            repo_path=repo_path,
            env=env,
            env_metadata=env.as_metadata_dict(),
            profile_name=profile_name,
            effective_targets=effective_targets,
            artifact_store=artifact_store,
            session=session,
            timeout_s=timeout_s,
        )
        all_task_outcomes[revision_type] = task_outcomes
        all_errors[revision_type] = errors
        ran_revisions.append(revision_type)

    # Build ExecutionEvidence
    overall_before = _aggregate_status(all_task_outcomes["before"])
    overall_after = _aggregate_status(all_task_outcomes["after"])

    before_rev_exec = None
    if "before" in ran_revisions:
        before_rev_exec = RevisionExecution(
            revision_type="before",
            profile=profile_name,
            overall_status=ValidationStatus(overall_before),
            task_outcomes=all_task_outcomes["before"],
            error_observations=all_errors["before"],
            env_metadata=env.as_metadata_dict(),
        )

    after_rev_exec = None
    if "after" in ran_revisions:
        after_rev_exec = RevisionExecution(
            revision_type="after",
            profile=profile_name,
            overall_status=ValidationStatus(overall_after),
            task_outcomes=all_task_outcomes["after"],
            error_observations=all_errors["after"],
            env_metadata=env.as_metadata_dict(),
        )

    # Add unavailable target outcomes to after_rev (the one agents inspect)
    unavailable_outcomes = _unavailable_task_outcomes(env)
    if after_rev_exec:
        after_rev_exec.task_outcomes.extend(unavailable_outcomes)

    exec_evidence = ExecutionEvidence(
        before=before_rev_exec,
        after=after_rev_exec,
    )

    # Advance bundle
    bundle.set_execution_evidence(exec_evidence)
    to_db(bundle, session)

    # Update repair_case status
    RepairCaseRepo(session).set_status(
        RepairCaseRepo(session).get_by_id(case_id),
        "EXECUTED",
    )

    total_errors = len(all_errors.get("after", []))
    log.info(
        "Case %s execution complete: status=%s errors=%d",
        case_id[:8], overall_after, total_errors,
    )

    return ExecutionResult(
        bundle=bundle,
        env_profile=env,
        ran_revisions=ran_revisions,
        total_errors=total_errors,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_revision(
    case_id: str,
    revision_type: str,
    repo_path: Path,
    env: EnvProfile,
    env_metadata: dict,
    profile_name: str,
    effective_targets: list[str],
    artifact_store: ArtifactStore,
    session: Session,
    timeout_s: int,
) -> tuple[list[TaskOutcome], list[EvidenceError]]:
    """Run all target tasks for one revision; persist to DB. Returns (task_outcomes, errors)."""
    run_repo = ExecutionRunRepo(session)
    task_repo = TaskResultRepo(session)
    error_repo = ErrorObservationRepo(session)

    exec_run = run_repo.create(
        repair_case_id=case_id,
        revision_type=revision_type,
        profile=profile_name,
        env_metadata=env_metadata,
    )

    task_outcomes: list[TaskOutcome] = []
    all_errors: list[EvidenceError] = []

    for target in effective_targets:
        gradle_tasks = tasks_for_target(target)
        results: list[GradleRunResult] = run_tasks(
            repo_path=repo_path,
            tasks=gradle_tasks,
            timeout_s=timeout_s,
        )

        for gr in results:
            # Write output to artifact store
            stdout_path, stdout_sha, stderr_path, stderr_sha = (
                artifact_store.write_task_output(
                    revision_type, gr.task_name, gr.stdout, gr.stderr
                )
            )

            # Persist task_result row
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

            # Persist error_observations
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

            # Build TaskOutcome for the bundle
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

    # Update execution_run timestamps
    exec_run.ended_at = datetime.now(timezone.utc)
    session.flush()

    return task_outcomes, all_errors


def _aggregate_status(task_outcomes: list[TaskOutcome]) -> str:
    """Compute overall status from individual task statuses."""
    if not task_outcomes:
        return ValidationStatus.NOT_RUN_YET.value
    statuses = [t.status for t in task_outcomes]
    if all(s == ValidationStatus.SUCCESS_REPOSITORY_LEVEL for s in statuses):
        return ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
    if any(s == ValidationStatus.FAILED_BUILD for s in statuses):
        return ValidationStatus.FAILED_BUILD.value
    if any(s == ValidationStatus.FAILED_TESTS for s in statuses):
        return ValidationStatus.FAILED_TESTS.value
    return ValidationStatus.INCONCLUSIVE.value


def _unavailable_task_outcomes(env: EnvProfile) -> list[TaskOutcome]:
    """Create NOT_RUN_ENVIRONMENT_UNAVAILABLE placeholders for unavailable targets."""
    outcomes = []
    for target, reason in env.unavailable_targets.items():
        outcomes.append(TaskOutcome(
            task_name=f"[{target}]",
            status=ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE,
            exit_code=None,
        ))
    return outcomes
