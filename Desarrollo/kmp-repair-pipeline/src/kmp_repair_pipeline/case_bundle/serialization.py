"""Serialize a CaseBundle to/from JSON and rehydrate from DB records.

Two persistence paths:
  1. JSON snapshot — save_snapshot / load_snapshot — full bundle to a single file,
     useful for debugging and inter-process handoff.
  2. DB rehydration — from_db_case — reconstruct a bundle from the normalized
     DB tables (the authoritative store).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..utils.json_io import load_json, save_json
from ..utils.log import get_logger
from .bundle import CaseBundle, CaseMeta
from .evidence import (
    ErrorObservation,
    ExecutionEvidence,
    ExplanationEvidence,
    LocalizationResult,
    PatchAttempt,
    RepairEvidence,
    RevisionExecution,
    StructuralEvidence,
    TaskOutcome,
    TargetValidation,
    UpdateEvidence,
    ValidationEvidence,
)
from ..domain.events import DependencyUpdateEvent, UpdateClass, VersionChange
from ..domain.validation import ValidationStatus

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# JSON snapshot
# ---------------------------------------------------------------------------


def save_snapshot(bundle: CaseBundle, path: str | Path) -> Path:
    """Write the full CaseBundle to a JSON snapshot file."""
    return save_json(bundle, path)


def load_snapshot(path: str | Path) -> CaseBundle:
    """Load a CaseBundle from a JSON snapshot file."""
    return load_json(CaseBundle, path)


# ---------------------------------------------------------------------------
# DB rehydration
# ---------------------------------------------------------------------------


def from_db_case(case_id: str, session: Session) -> Optional[CaseBundle]:
    """Reconstruct a CaseBundle from DB records.

    Returns None if the case_id does not exist in the DB.
    """
    from ..storage.models import RepairCase
    from ..storage.repositories import (
        DependencyDiffRepo,
        DependencyEventRepo,
        ErrorObservationRepo,
        ExecutionRunRepo,
        LocalizationCandidateRepo,
        PatchAttemptRepo,
        RepairCaseRepo,
        RepositoryRepo,
        SourceEntityRepo,
        TaskResultRepo,
        ValidationRunRepo,
    )

    case = RepairCaseRepo(session).get_by_id(case_id)
    if case is None:
        log.warning(f"Case {case_id} not found in DB")
        return None

    event = DependencyEventRepo(session).get_by_id(case.dependency_event_id)
    repo = RepositoryRepo(session).get_by_id(event.repository_id)

    # Meta
    meta = CaseMeta(
        case_id=case.id,
        event_id=event.id,
        repository_url=repo.url,
        repository_name=repo.name or "",
        artifact_dir=case.artifact_dir or "",
        status=case.status,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )

    # Update evidence
    diffs = DependencyDiffRepo(session).list_for_event(event.id)
    version_changes = [
        VersionChange(
            dependency_group=d.dependency_group,
            version_key=d.version_key or "",
            before=d.version_before,
            after=d.version_after,
        )
        for d in diffs
    ]
    update_ev = UpdateEvidence(
        update_event=DependencyUpdateEvent(
            repo_url=repo.url,
            repo_local_path="",
            pr_ref=event.pr_ref,
            version_changes=version_changes,
            update_class=UpdateClass(event.update_class),
        ),
        version_changes=version_changes,
        update_class=UpdateClass(event.update_class),
        detection_source=event.source,
        detected_at=event.created_at,
    )

    # Execution evidence
    execution_ev: Optional[ExecutionEvidence] = None
    runs = ExecutionRunRepo(session).list_for_case(case.id)
    if runs:
        execution_ev = ExecutionEvidence()
        for run in runs:
            tasks = TaskResultRepo(session).list_for_run(run.id)
            task_outcomes = []
            all_errors = []
            for task in tasks:
                errors = ErrorObservationRepo(session).list_for_task(task.id)
                err_obs = [
                    ErrorObservation(
                        error_type=e.error_type,
                        file_path=e.file_path,
                        line=e.line,
                        column=e.column,
                        message=e.message,
                        raw_text=e.raw_text,
                        parser=e.parser,
                    )
                    for e in errors
                ]
                task_outcomes.append(TaskOutcome(
                    task_name=task.task_name,
                    exit_code=task.exit_code,
                    status=ValidationStatus(task.status) if task.status else ValidationStatus.NOT_RUN_YET,
                    duration_s=task.duration_s,
                    stdout_path=task.stdout_path,
                    stderr_path=task.stderr_path,
                    stdout_sha256=task.stdout_sha256,
                    stderr_sha256=task.stderr_sha256,
                ))
                all_errors.extend(err_obs)

            rev_exec = RevisionExecution(
                revision_type=run.revision_type,
                profile=run.profile,
                overall_status=ValidationStatus(run.status) if run.status else ValidationStatus.NOT_RUN_YET,
                task_outcomes=task_outcomes,
                error_observations=all_errors,
                env_metadata=run.env_metadata or {},
                started_at=run.started_at,
                ended_at=run.ended_at,
            )
            if run.revision_type == "before":
                execution_ev.before = rev_exec
            elif run.revision_type == "after":
                execution_ev.after = rev_exec

    # Structural evidence — build from source_entities
    structural_ev: Optional[StructuralEvidence] = None
    source_entities = SourceEntityRepo(session).list_for_case(case.id)
    if source_entities:
        source_set_map_data: dict[str, list[str]] = {}
        for ent in source_entities:
            source_set_map_data.setdefault(ent.source_set, []).append(ent.file_path)
        from .evidence import SourceSetMap
        ssm = SourceSetMap(
            common_files=source_set_map_data.get("common", []),
            android_files=source_set_map_data.get("android", []),
            ios_files=source_set_map_data.get("ios", []),
            jvm_files=source_set_map_data.get("jvm", []),
            other={k: v for k, v in source_set_map_data.items()
                   if k not in ("common", "android", "ios", "jvm")},
        )
        structural_ev = StructuralEvidence(
            source_set_map=ssm,
            total_kotlin_files=len(set(e.file_path for e in source_entities)),
        )

    # Repair evidence — localization candidates and patch attempts
    repair_ev: Optional[RepairEvidence] = None
    candidates = LocalizationCandidateRepo(session).list_for_case_ranked(case.id)
    patch_db_records = PatchAttemptRepo(session).list_for_case(case.id)
    if candidates or patch_db_records:
        repair_ev = RepairEvidence()
        if candidates:
            repair_ev.localization = LocalizationResult(
                candidates=[
                    LocalizationResult.Candidate(
                        rank=c.rank,
                        file_path=c.file_path or "",
                        source_set=c.source_set or "unknown",
                        classification=c.classification,
                        score=c.score,
                        score_breakdown=c.score_breakdown or {},
                    )
                    for c in candidates
                ]
            )
        for p in patch_db_records:
            repair_ev.patch_attempts.append(PatchAttempt(
                attempt_number=p.attempt_number,
                repair_mode=p.repair_mode,
                status=p.status,
                diff_path=p.diff_path,
                diff_sha256=p.diff_sha256,
                touched_files=p.touched_files or [],
                prompt_path=p.prompt_path,
                response_path=p.response_path,
                model_id=p.model_id,
                tokens_in=p.tokens_in,
                tokens_out=p.tokens_out,
                retry_reason=p.retry_reason,
            ))

    # Validation evidence
    validation_ev: Optional[ValidationEvidence] = None
    if patch_db_records:
        last_patch = patch_db_records[-1]
        vrun_records = ValidationRunRepo(session).list_for_patch(last_patch.id)
        if vrun_records:
            validation_ev = ValidationEvidence(
                target_results=[
                    TargetValidation(
                        target=vr.target,
                        status=ValidationStatus(vr.status),
                        unavailable_reason=vr.unavailable_reason,
                        patch_attempt_number=last_patch.attempt_number,
                        duration_s=vr.duration_s,
                    )
                    for vr in vrun_records
                ],
            )
            # Aggregate to repository level
            statuses = [r.status for r in validation_ev.target_results]
            if all(s == ValidationStatus.SUCCESS_REPOSITORY_LEVEL for s in statuses):
                validation_ev.repository_level_status = ValidationStatus.SUCCESS_REPOSITORY_LEVEL
            elif any(s == ValidationStatus.FAILED_BUILD for s in statuses):
                validation_ev.repository_level_status = ValidationStatus.FAILED_BUILD
            elif any(s == ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE for s in statuses):
                validation_ev.repository_level_status = ValidationStatus.PARTIAL_SUCCESS
            else:
                validation_ev.repository_level_status = ValidationStatus.INCONCLUSIVE

    bundle = CaseBundle(
        meta=meta,
        update_evidence=update_ev,
        execution=execution_ev,
        structural=structural_ev,
        repair=repair_ev,
        validation=validation_ev,
    )
    log.info(f"Rehydrated: {bundle.summary()}")
    return bundle


def to_db(bundle: CaseBundle, session: Session) -> None:
    """Persist the bundle status back to the DB (lightweight sync).

    Full artifact writes (execution results, patches, etc.) are done by
    the individual stage orchestrators via the repository classes directly.
    This method just keeps repair_cases.status in sync.
    """
    from ..storage.repositories import RepairCaseRepo
    case = RepairCaseRepo(session).get_by_id(bundle.case_id)
    if case is None:
        log.warning(f"Cannot sync bundle status: case {bundle.case_id} not in DB")
        return
    case.status = bundle.meta.status
    session.flush()
    log.debug(f"Synced case {bundle.case_id[:8]} status → {bundle.meta.status}")
