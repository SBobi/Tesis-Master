"""RQ job enqueueing and execution for pipeline web operations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rq.job import Job
from sqlalchemy.orm import Session

from ..storage.db import get_session_factory
from ..storage.repositories import (
    CaseStatusTransitionRepo,
    PipelineJobRepo,
    RepairCaseRepo,
)
from ..utils.log import get_logger
from .orchestrator import pipeline_plan, run_stage_with_audit
from .queue import get_queue, get_redis_connection
from .settings import get_settings
from .stages import (
    command_for_pipeline,
    command_for_stage,
    sanitize_pipeline_request,
    sanitize_stage_params,
)

log = get_logger(__name__)


def enqueue_stage_job(
    session: Session,
    case_id: str,
    stage: str,
    params: dict[str, Any] | None,
    requested_by: str | None,
):
    """Create a RUN_STAGE job row and enqueue it in RQ."""
    effective_params = sanitize_stage_params(stage, params)
    command_preview = command_for_stage(case_id, stage, effective_params)

    job_repo = PipelineJobRepo(session)
    case_repo = RepairCaseRepo(session)
    transition_repo = CaseStatusTransitionRepo(session)

    case_row = case_repo.get_by_id(case_id)
    if case_row is None:
        raise ValueError(f"Case {case_id} no existe")

    log_path = _build_job_log_path(case_id)
    job_row = job_repo.create(
        repair_case_id=case_id,
        job_type="RUN_STAGE",
        stage=stage,
        requested_by=requested_by,
        command_preview=command_preview,
        params=params or {},
        effective_params={stage: effective_params},
        log_path=str(log_path),
    )

    rq_job = get_queue().enqueue(execute_pipeline_job, job_row.id)
    job_row.rq_job_id = rq_job.id

    transition_repo.create(
        repair_case_id=case_id,
        pipeline_job_id=job_row.id,
        stage=stage,
        from_status=case_row.status,
        to_status=case_row.status,
        transition_type="JOB_QUEUED",
        message="Job encolado",
        metadata_json={"rq_job_id": rq_job.id, "command": command_preview, "effective_params": {stage: effective_params}},
    )

    session.flush()
    return job_row


def enqueue_pipeline_job(
    session: Session,
    case_id: str,
    start_from_stage: str | None,
    params_by_stage: dict[str, dict[str, Any]] | None,
    requested_by: str | None,
):
    """Create a RUN_PIPELINE job row and enqueue it in RQ."""
    start, effective_map = sanitize_pipeline_request(start_from_stage, params_by_stage)
    command_preview = command_for_pipeline(case_id, start, effective_map)

    job_repo = PipelineJobRepo(session)
    case_repo = RepairCaseRepo(session)
    transition_repo = CaseStatusTransitionRepo(session)

    case_row = case_repo.get_by_id(case_id)
    if case_row is None:
        raise ValueError(f"Case {case_id} no existe")

    log_path = _build_job_log_path(case_id)
    job_row = job_repo.create(
        repair_case_id=case_id,
        job_type="RUN_PIPELINE",
        start_from_stage=start,
        requested_by=requested_by,
        command_preview=command_preview,
        params=params_by_stage or {},
        effective_params=effective_map,
        log_path=str(log_path),
    )

    rq_job = get_queue().enqueue(execute_pipeline_job, job_row.id)
    job_row.rq_job_id = rq_job.id

    transition_repo.create(
        repair_case_id=case_id,
        pipeline_job_id=job_row.id,
        stage=start,
        from_status=case_row.status,
        to_status=case_row.status,
        transition_type="JOB_QUEUED",
        message="Pipeline encolado",
        metadata_json={"rq_job_id": rq_job.id, "command": command_preview, "effective_params": effective_map},
    )

    session.flush()
    return job_row


def request_job_cancel(session: Session, pipeline_job_id: str):
    """Request cancellation for a queued/running job."""
    job_repo = PipelineJobRepo(session)
    transition_repo = CaseStatusTransitionRepo(session)

    job_row = job_repo.get_by_id(pipeline_job_id)
    if job_row is None:
        raise ValueError(f"Job {pipeline_job_id} no existe")

    previous_status = job_row.status
    if previous_status in ("SUCCEEDED", "FAILED", "CANCELED"):
        return job_row

    job_row.cancel_requested = True
    job_row.status = "CANCEL_REQUESTED"
    job_row.updated_at = _now()

    transition_repo.create(
        repair_case_id=job_row.repair_case_id,
        pipeline_job_id=job_row.id,
        stage=job_row.current_stage,
        from_status=previous_status,
        to_status="CANCEL_REQUESTED",
        transition_type="JOB_CANCEL_REQUESTED",
        message="Cancelación solicitada",
    )

    if job_row.rq_job_id:
        conn = get_redis_connection()
        try:
            rq_job = Job.fetch(job_row.rq_job_id, connection=conn)
            rq_job.cancel()
        except Exception:
            pass

        try:
            from rq.command import send_stop_job_command

            send_stop_job_command(conn, job_row.rq_job_id)
        except Exception:
            pass

    session.flush()
    return job_row


def execute_pipeline_job(pipeline_job_id: str) -> None:
    """RQ worker entrypoint: executes one stage or a full pipeline chain."""
    factory = get_session_factory()
    session = factory()

    try:
        job_repo = PipelineJobRepo(session)
        case_repo = RepairCaseRepo(session)
        transition_repo = CaseStatusTransitionRepo(session)

        job_row = job_repo.get_by_id(pipeline_job_id)
        if job_row is None:
            log.error("PipelineJob %s no existe", pipeline_job_id)
            return

        case_row = case_repo.get_by_id(job_row.repair_case_id)
        if case_row is None:
            job_row.status = "FAILED"
            job_row.error_message = f"Case {job_row.repair_case_id} no existe"
            job_row.finished_at = _now()
            session.commit()
            return

        job_row.status = "RUNNING"
        job_row.started_at = _now()
        job_row.updated_at = _now()
        _append_log(job_row.log_path, f"[job] inicio {job_row.job_type} case={job_row.repair_case_id}")

        transition_repo.create(
            repair_case_id=job_row.repair_case_id,
            pipeline_job_id=job_row.id,
            stage=job_row.stage or job_row.start_from_stage,
            from_status="QUEUED",
            to_status="RUNNING",
            transition_type="JOB_STARTED",
            message="Worker tomó el job",
            metadata_json={"rq_job_id": job_row.rq_job_id},
        )
        session.commit()

        planned_stages = _resolve_stages(job_row)
        result_summary: dict[str, Any] = {}

        for stage in planned_stages:
            session.expire_all()
            job_row = job_repo.get_by_id(pipeline_job_id)
            if job_row is None:
                return

            if job_row.cancel_requested:
                job_row.status = "CANCELED"
                job_row.finished_at = _now()
                job_row.current_stage = None
                job_row.updated_at = _now()
                transition_repo.create(
                    repair_case_id=job_row.repair_case_id,
                    pipeline_job_id=job_row.id,
                    stage=job_row.current_stage,
                    from_status="RUNNING",
                    to_status="CANCELED",
                    transition_type="JOB_CANCELED",
                    message="Job cancelado por solicitud de usuario",
                )
                _append_log(job_row.log_path, "[job] cancelado")
                session.commit()
                return

            job_row.current_stage = stage
            job_row.updated_at = _now()
            session.flush()

            stage_params = (job_row.effective_params or {}).get(stage, {})

            def _stage_log(message: str) -> None:
                _append_log(job_row.log_path, message)

            stage_result = run_stage_with_audit(
                session=session,
                case_id=job_row.repair_case_id,
                stage=stage,
                params=stage_params,
                pipeline_job_id=job_row.id,
                log=_stage_log,
            )
            result_summary[stage] = {
                "duration_s": stage_result.duration_s,
                "case_status": stage_result.case_status,
                "summary": stage_result.summary,
            }

            job_row.result_summary = result_summary
            job_row.updated_at = _now()
            session.commit()

        session.expire_all()
        job_row = job_repo.get_by_id(pipeline_job_id)
        if job_row is None:
            return

        job_row.status = "SUCCEEDED"
        job_row.finished_at = _now()
        job_row.current_stage = None
        job_row.result_summary = result_summary
        job_row.updated_at = _now()

        transition_repo.create(
            repair_case_id=job_row.repair_case_id,
            pipeline_job_id=job_row.id,
            stage=job_row.stage or job_row.start_from_stage,
            from_status="RUNNING",
            to_status="SUCCEEDED",
            transition_type="JOB_COMPLETED",
            message="Job completado",
            metadata_json={"result_summary": result_summary},
        )
        _append_log(job_row.log_path, "[job] completado")
        session.commit()

    except Exception as exc:
        session.rollback()
        try:
            job_repo = PipelineJobRepo(session)
            transition_repo = CaseStatusTransitionRepo(session)
            job_row = job_repo.get_by_id(pipeline_job_id)
            if job_row is not None:
                job_row.status = "FAILED"
                job_row.error_message = str(exc)
                job_row.finished_at = _now()
                job_row.current_stage = None
                job_row.updated_at = _now()
                _append_log(job_row.log_path, f"[job] ERROR: {exc}")
                transition_repo.create(
                    repair_case_id=job_row.repair_case_id,
                    pipeline_job_id=job_row.id,
                    stage=job_row.current_stage,
                    from_status="RUNNING",
                    to_status="FAILED",
                    transition_type="JOB_FAILED",
                    message=str(exc),
                )
                session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()


def _resolve_stages(job_row) -> list[str]:
    if job_row.job_type == "RUN_STAGE":
        if not job_row.stage:
            raise ValueError("RUN_STAGE requiere campo stage")
        return [job_row.stage]

    if job_row.job_type == "RUN_PIPELINE":
        start = job_row.start_from_stage or "build-case"
        return pipeline_plan(start)

    raise ValueError(f"Tipo de job no soportado: {job_row.job_type}")


def _build_job_log_path(case_id: str) -> Path:
    settings = get_settings()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_dir = settings.artifact_base / case_id / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"job-{timestamp}.log"


def _append_log(log_path: str | None, message: str) -> None:
    if not log_path:
        return
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_now().isoformat()} {message}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _now() -> datetime:
    return datetime.now(timezone.utc)
