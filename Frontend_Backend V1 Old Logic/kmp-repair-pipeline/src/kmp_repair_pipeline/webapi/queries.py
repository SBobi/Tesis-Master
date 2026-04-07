"""Query helpers for web API responses."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, exists, or_, select
from sqlalchemy.orm import Session

from ..storage.models import (
    AgentLog,
    CaseStatusTransition,
    DependencyDiff,
    DependencyEvent,
    EvaluationMetric,
    ExecutionRun,
    Explanation,
    LocalizationCandidate,
    PatchAttempt,
    PipelineJob,
    RepairCase,
    Repository,
    Revision,
    SourceEntity,
    TaskResult,
    ValidationRun,
)
from .stages import PIPELINE_STAGES


def list_cases(
    session: Session,
    *,
    status: str | None,
    update_class: str | None,
    repo: str | None,
    date_from: datetime | None,
    repair_mode: str | None,
) -> list[dict[str, Any]]:
    stmt = (
        select(RepairCase, DependencyEvent, Repository)
        .join(DependencyEvent, RepairCase.dependency_event_id == DependencyEvent.id)
        .join(Repository, DependencyEvent.repository_id == Repository.id)
        .order_by(RepairCase.created_at.desc())
    )

    if status:
        stmt = stmt.where(RepairCase.status == status)
    if update_class:
        stmt = stmt.where(DependencyEvent.update_class == update_class)
    if repo:
        like = f"%{repo}%"
        stmt = stmt.where(
            or_(
                Repository.url.ilike(like),
                Repository.name.ilike(like),
                Repository.owner.ilike(like),
            )
        )
    if date_from:
        stmt = stmt.where(RepairCase.created_at >= date_from)
    if repair_mode:
        has_mode = exists(
            select(PatchAttempt.id).where(
                and_(
                    PatchAttempt.repair_case_id == RepairCase.id,
                    PatchAttempt.repair_mode == repair_mode,
                )
            )
        )
        stmt = stmt.where(has_mode)

    rows = session.execute(stmt).all()
    items: list[dict[str, Any]] = []

    for case_row, event_row, repo_row in rows:
        latest_attempt = session.scalars(
            select(PatchAttempt)
            .where(PatchAttempt.repair_case_id == case_row.id)
            .order_by(PatchAttempt.created_at.desc())
        ).first()

        active_job = session.scalars(
            select(PipelineJob)
            .where(
                PipelineJob.repair_case_id == case_row.id,
                PipelineJob.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
            )
            .order_by(PipelineJob.created_at.desc())
        ).first()

        items.append(
            {
                "case_id": case_row.id,
                "status": case_row.status,
                "created_at": case_row.created_at,
                "updated_at": case_row.updated_at,
                "repository": {
                    "url": repo_row.url,
                    "owner": repo_row.owner,
                    "name": repo_row.name,
                },
                "event": {
                    "pr_ref": event_row.pr_ref,
                    "pr_title": event_row.pr_title,
                    "update_class": event_row.update_class,
                },
                "latest_repair_mode": latest_attempt.repair_mode if latest_attempt else None,
                "latest_patch_status": latest_attempt.status if latest_attempt else None,
                "active_job": serialize_job(active_job) if active_job else None,
            }
        )

    return items


def get_case_detail(session: Session, case_id: str) -> dict[str, Any] | None:
    row = session.execute(
        select(RepairCase, DependencyEvent, Repository)
        .join(DependencyEvent, RepairCase.dependency_event_id == DependencyEvent.id)
        .join(Repository, DependencyEvent.repository_id == Repository.id)
        .where(RepairCase.id == case_id)
    ).first()
    if row is None:
        return None

    case_row, event_row, repo_row = row

    diffs = list(
        session.scalars(
            select(DependencyDiff)
            .where(DependencyDiff.dependency_event_id == event_row.id)
            .order_by(DependencyDiff.created_at)
        ).all()
    )
    revisions = list(
        session.scalars(
            select(Revision)
            .where(Revision.repair_case_id == case_id)
            .order_by(Revision.created_at)
        ).all()
    )
    execution_runs = list(
        session.scalars(
            select(ExecutionRun)
            .where(ExecutionRun.repair_case_id == case_id)
            .order_by(ExecutionRun.created_at)
        ).all()
    )
    source_entities = list(
        session.scalars(
            select(SourceEntity)
            .where(SourceEntity.repair_case_id == case_id)
            .order_by(SourceEntity.created_at)
        ).all()
    )
    localization_candidates = list(
        session.scalars(
            select(LocalizationCandidate)
            .where(LocalizationCandidate.repair_case_id == case_id)
            .order_by(LocalizationCandidate.rank)
        ).all()
    )
    patch_attempts = list(
        session.scalars(
            select(PatchAttempt)
            .where(PatchAttempt.repair_case_id == case_id)
            .order_by(PatchAttempt.created_at)
        ).all()
    )
    validation_runs = list(
        session.scalars(
            select(ValidationRun)
            .where(ValidationRun.repair_case_id == case_id)
            .order_by(ValidationRun.created_at)
        ).all()
    )
    explanations = list(
        session.scalars(
            select(Explanation)
            .where(Explanation.repair_case_id == case_id)
            .order_by(Explanation.created_at)
        ).all()
    )
    agent_logs = list(
        session.scalars(
            select(AgentLog)
            .where(AgentLog.repair_case_id == case_id)
            .order_by(AgentLog.created_at)
        ).all()
    )
    metrics = list(
        session.scalars(
            select(EvaluationMetric)
            .where(EvaluationMetric.repair_case_id == case_id)
            .order_by(EvaluationMetric.repair_mode)
        ).all()
    )
    jobs = list(
        session.scalars(
            select(PipelineJob)
            .where(PipelineJob.repair_case_id == case_id)
            .order_by(PipelineJob.created_at.desc())
        ).all()
    )
    transitions = list(
        session.scalars(
            select(CaseStatusTransition)
            .where(CaseStatusTransition.repair_case_id == case_id)
            .order_by(CaseStatusTransition.created_at.desc())
        ).all()
    )

    tasks_by_run: dict[str, list[TaskResult]] = {}
    if execution_runs:
        run_ids = [r.id for r in execution_runs]
        all_tasks = list(
            session.scalars(
                select(TaskResult).where(TaskResult.execution_run_id.in_(run_ids)).order_by(TaskResult.created_at)
            ).all()
        )
        for task in all_tasks:
            tasks_by_run.setdefault(task.execution_run_id, []).append(task)

    active_job = next((j for j in jobs if j.status in ("QUEUED", "RUNNING", "CANCEL_REQUESTED")), None)

    timeline = build_timeline(
        case_row=case_row,
        revisions=revisions,
        execution_runs=execution_runs,
        source_entities=source_entities,
        localization_candidates=localization_candidates,
        patch_attempts=patch_attempts,
        validation_runs=validation_runs,
        explanations=explanations,
        metrics=metrics,
        transitions=transitions,
        active_job=active_job,
    )

    return {
        "case": {
            "case_id": case_row.id,
            "status": case_row.status,
            "artifact_dir": case_row.artifact_dir,
            "created_at": case_row.created_at,
            "updated_at": case_row.updated_at,
            "repository": {
                "url": repo_row.url,
                "owner": repo_row.owner,
                "name": repo_row.name,
            },
            "event": {
                "pr_ref": event_row.pr_ref,
                "pr_title": event_row.pr_title,
                "update_class": event_row.update_class,
                "raw_diff": event_row.raw_diff,
            },
        },
        "timeline": timeline,
        "evidence": {
            "update_evidence": {
                "changes": [
                    {
                        "dependency_group": d.dependency_group,
                        "version_key": d.version_key,
                        "before": d.version_before,
                        "after": d.version_after,
                    }
                    for d in diffs
                ],
                "raw_diff": event_row.raw_diff,
            },
            "execution_before_after": [
                {
                    "run_id": run.id,
                    "revision_type": run.revision_type,
                    "status": run.status,
                    "profile": run.profile,
                    "started_at": run.started_at,
                    "ended_at": run.ended_at,
                    "duration_s": run.duration_s,
                    "env_metadata": run.env_metadata,
                    "tasks": [
                        {
                            "task_id": task.id,
                            "task_name": task.task_name,
                            "status": task.status,
                            "exit_code": task.exit_code,
                            "duration_s": task.duration_s,
                            "stdout_path": task.stdout_path,
                            "stderr_path": task.stderr_path,
                        }
                        for task in tasks_by_run.get(run.id, [])
                    ],
                }
                for run in execution_runs
            ],
            "structural_evidence": {
                "source_entities_count": len(source_entities),
                "sample": [
                    {
                        "file_path": se.file_path,
                        "source_set": se.source_set,
                        "fqcn": se.fqcn,
                        "is_expect": se.is_expect,
                        "is_actual": se.is_actual,
                    }
                    for se in source_entities[:40]
                ],
            },
            "localization_ranking": [
                {
                    "rank": c.rank,
                    "file_path": c.file_path,
                    "source_set": c.source_set,
                    "classification": c.classification,
                    "score": c.score,
                    "score_breakdown": c.score_breakdown,
                }
                for c in localization_candidates
            ],
            "patch_attempts": [
                {
                    "id": p.id,
                    "attempt_number": p.attempt_number,
                    "repair_mode": p.repair_mode,
                    "status": p.status,
                    "diff_path": p.diff_path,
                    "diff_preview": read_text_preview(p.diff_path),
                    "touched_files": p.touched_files,
                    "retry_reason": p.retry_reason,
                    "created_at": p.created_at,
                }
                for p in patch_attempts
            ],
            "validation_by_target": [
                {
                    "id": vr.id,
                    "patch_attempt_id": vr.patch_attempt_id,
                    "target": vr.target,
                    "status": vr.status,
                    "unavailable_reason": vr.unavailable_reason,
                    "started_at": vr.started_at,
                    "ended_at": vr.ended_at,
                    "duration_s": vr.duration_s,
                    "execution_run_id": vr.execution_run_id,
                }
                for vr in validation_runs
            ],
            "explanations": [
                {
                    "id": ex.id,
                    "json_path": ex.json_path,
                    "json_preview": read_text_preview(ex.json_path),
                    "markdown_path": ex.markdown_path,
                    "markdown_preview": read_text_preview(ex.markdown_path),
                    "model_id": ex.model_id,
                    "tokens_in": ex.tokens_in,
                    "tokens_out": ex.tokens_out,
                    "created_at": ex.created_at,
                }
                for ex in explanations
            ],
            "agent_logs": [
                {
                    "id": al.id,
                    "agent_type": al.agent_type,
                    "call_index": al.call_index,
                    "model_id": al.model_id,
                    "tokens_in": al.tokens_in,
                    "tokens_out": al.tokens_out,
                    "latency_s": al.latency_s,
                    "prompt_path": al.prompt_path,
                    "response_path": al.response_path,
                    "error": al.error,
                    "created_at": al.created_at,
                }
                for al in agent_logs
            ],
            "metrics": [
                {
                    "repair_mode": m.repair_mode,
                    "bsr": m.bsr,
                    "ctsr": m.ctsr,
                    "ffsr": m.ffsr,
                    "efr": m.efr,
                    "hit_at_1": m.hit_at_1,
                    "hit_at_3": m.hit_at_3,
                    "hit_at_5": m.hit_at_5,
                    "source_set_accuracy": m.source_set_accuracy,
                    "extra": m.extra,
                    "updated_at": m.updated_at,
                }
                for m in metrics
            ],
        },
        "jobs": [serialize_job(j) for j in jobs],
        "history": [serialize_transition(t) for t in transitions],
    }


def get_case_history(session: Session, case_id: str) -> dict[str, Any]:
    transitions = list(
        session.scalars(
            select(CaseStatusTransition)
            .where(CaseStatusTransition.repair_case_id == case_id)
            .order_by(CaseStatusTransition.created_at.desc())
        ).all()
    )
    jobs = list(
        session.scalars(
            select(PipelineJob)
            .where(PipelineJob.repair_case_id == case_id)
            .order_by(PipelineJob.created_at.desc())
        ).all()
    )
    return {
        "case_id": case_id,
        "transitions": [serialize_transition(t) for t in transitions],
        "jobs": [serialize_job(j) for j in jobs],
    }


def list_active_jobs(session: Session) -> list[dict[str, Any]]:
    jobs = list(
        session.scalars(
            select(PipelineJob)
            .where(PipelineJob.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]))
            .order_by(PipelineJob.created_at.desc())
        ).all()
    )
    return [serialize_job(j) for j in jobs]


def serialize_job(job: PipelineJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "job_id": job.id,
        "case_id": job.repair_case_id,
        "job_type": job.job_type,
        "stage": job.stage,
        "start_from_stage": job.start_from_stage,
        "status": job.status,
        "rq_job_id": job.rq_job_id,
        "requested_by": job.requested_by,
        "command_preview": job.command_preview,
        "params": job.params,
        "effective_params": job.effective_params,
        "cancel_requested": job.cancel_requested,
        "current_stage": job.current_stage,
        "error_message": job.error_message,
        "result_summary": job.result_summary,
        "log_path": job.log_path,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def serialize_transition(t: CaseStatusTransition) -> dict[str, Any]:
    return {
        "transition_id": t.id,
        "case_id": t.repair_case_id,
        "pipeline_job_id": t.pipeline_job_id,
        "stage": t.stage,
        "from_status": t.from_status,
        "to_status": t.to_status,
        "transition_type": t.transition_type,
        "message": t.message,
        "metadata": t.metadata_json,
        "created_at": t.created_at,
    }


def build_timeline(
    *,
    case_row: RepairCase,
    revisions: list[Revision],
    execution_runs: list[ExecutionRun],
    source_entities: list[SourceEntity],
    localization_candidates: list[LocalizationCandidate],
    patch_attempts: list[PatchAttempt],
    validation_runs: list[ValidationRun],
    explanations: list[Explanation],
    metrics: list[EvaluationMetric],
    transitions: list[CaseStatusTransition],
    active_job: PipelineJob | None,
) -> list[dict[str, Any]]:
    stage_map: dict[str, dict[str, Any]] = {
        stage: {
            "stage": stage,
            "status": "NOT_STARTED",
            "duration_s": None,
            "action": "run",
            "has_evidence": False,
        }
        for stage in PIPELINE_STAGES
    }

    stage_map["ingest"]["status"] = "COMPLETED"
    stage_map["ingest"]["has_evidence"] = True

    has_before = any(r.revision_type == "before" and r.local_path for r in revisions)
    has_after = any(r.revision_type == "after" and r.local_path for r in revisions)
    if has_before and has_after:
        stage_map["build-case"].update({"status": "COMPLETED", "has_evidence": True})

    has_before_after_runs = any(r.revision_type in ("before", "after") for r in execution_runs)
    if has_before_after_runs:
        stage_map["run-before-after"].update({"status": "COMPLETED", "has_evidence": True})

    if source_entities:
        stage_map["analyze-case"].update({"status": "COMPLETED", "has_evidence": True})

    if localization_candidates:
        stage_map["localize"].update({"status": "COMPLETED", "has_evidence": True})

    if patch_attempts:
        stage_map["repair"].update({"status": "COMPLETED", "has_evidence": True, "action": "retry"})

    if validation_runs:
        stage_map["validate"].update({"status": "COMPLETED", "has_evidence": True, "action": "retry"})

    if explanations:
        stage_map["explain"].update({"status": "COMPLETED", "has_evidence": True, "action": "retry"})

    if metrics:
        stage_map["metrics"].update({"status": "COMPLETED", "has_evidence": True, "action": "retry"})

    # Apply transition-derived stage status and durations.
    latest_by_stage: dict[str, CaseStatusTransition] = {}
    for tr in transitions:
        if tr.stage and tr.stage not in latest_by_stage:
            latest_by_stage[tr.stage] = tr

    for stage, tr in latest_by_stage.items():
        if stage not in stage_map:
            continue
        if tr.transition_type == "STAGE_FAILED":
            stage_map[stage]["status"] = "FAILED"
            stage_map[stage]["action"] = "retry"
        elif tr.transition_type == "STAGE_COMPLETED":
            stage_map[stage]["status"] = "COMPLETED"
            stage_map[stage]["action"] = "retry"
        elif tr.transition_type == "STAGE_STARTED":
            stage_map[stage]["status"] = "RUNNING"

        metadata = tr.metadata_json or {}
        if isinstance(metadata, dict) and metadata.get("duration_s") is not None:
            stage_map[stage]["duration_s"] = metadata.get("duration_s")

    if active_job and active_job.current_stage and active_job.current_stage in stage_map:
        stage_map[active_job.current_stage]["status"] = "RUNNING"

    if case_row.status == "FAILED":
        failed_transition = next((t for t in transitions if t.transition_type == "STAGE_FAILED"), None)
        if failed_transition and failed_transition.stage in stage_map:
            stage_map[failed_transition.stage]["status"] = "FAILED"
            stage_map[failed_transition.stage]["action"] = "retry"

    return [stage_map[s] for s in PIPELINE_STAGES]


def read_text_preview(path: str | None, max_chars: int = 4000) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text) > max_chars:
        return text[:max_chars] + "\n... [truncated]"
    return text


def read_tail_lines(path: str | None, lines: int = 200) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists() or not p.is_file():
        return []
    try:
        content = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]
