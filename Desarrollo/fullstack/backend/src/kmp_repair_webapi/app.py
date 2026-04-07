"""FastAPI app to operate kmp-repair-pipeline end-to-end from the web.

All pipeline business logic imports from kmp_repair_pipeline.* — the
canonical pipeline installed as an editable local dependency.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

# ── Canonical pipeline imports (via editable install) ─────────────────────
from kmp_repair_pipeline.ingest.event_builder import ingest_pr_url
from kmp_repair_pipeline.storage.db import get_session_factory
from kmp_repair_pipeline.storage.models import EvaluationMetric
from kmp_repair_pipeline.storage.repositories import CaseStatusTransitionRepo, PipelineJobRepo, RepairCaseRepo
# ──────────────────────────────────────────────────────────────────────────

from .env_loader import load_project_env
from .job_runner import enqueue_pipeline_job, enqueue_stage_job, request_job_cancel
from .queries import (
    get_case_detail,
    get_case_history,
    list_active_jobs,
    list_cases,
    read_tail_lines,
    serialize_job,
)
from .schemas import (
    CancelJobRequest,
    CreateCaseRequest,
    RunPipelineRequest,
    RunStageRequest,
)
from .settings import get_settings

# Ensure .env is loaded before app/settings are instantiated.
load_project_env()


def get_db() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="kmp-repair-pipeline web",
        version="0.1.0",
        description="API para operar el pipeline KMP con trazabilidad y streaming en vivo.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "kmp-repair-pipeline-web",
            "time": datetime.now(timezone.utc).isoformat(),
        }

    @app.post("/api/cases")
    def create_case(payload: CreateCaseRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            result = ingest_pr_url(
                pr_url=payload.pr_url,
                session=session,
                artifact_dir=payload.artifact_dir,
                detection_source=payload.detection_source,
            )
            if result.skipped:
                session.rollback()
                raise HTTPException(status_code=422, detail=result.skip_reason)

            case_row = RepairCaseRepo(session).get_by_id(result.case_id)
            if case_row is None:
                raise HTTPException(status_code=500, detail="No se pudo recuperar el caso creado")

            CaseStatusTransitionRepo(session).create(
                repair_case_id=result.case_id,
                stage="ingest",
                from_status=None,
                to_status=case_row.status,
                transition_type="INGEST_CREATED",
                message="Caso creado desde PR URL",
                metadata_json={
                    "pr_url": payload.pr_url,
                    "update_class": result.update_class.value,
                    "version_changes": [vc.model_dump() for vc in result.version_changes],
                },
            )
            session.commit()

            detail = get_case_detail(session, result.case_id)
            if detail is None:
                raise HTTPException(status_code=500, detail="Caso creado pero no se pudo hidratar detalle")
            return detail
        except HTTPException:
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/cases")
    def list_cases_endpoint(
        status: str | None = Query(default=None),
        update_class: str | None = Query(default=None),
        repo: str | None = Query(default=None),
        date_from: datetime | None = Query(default=None),
        repair_mode: str | None = Query(default=None),
        session: Session = Depends(get_db),
    ) -> dict[str, Any]:
        items = list_cases(
            session,
            status=status,
            update_class=update_class,
            repo=repo,
            date_from=date_from,
            repair_mode=repair_mode,
        )
        return {"items": items, "count": len(items)}

    @app.get("/api/cases/{case_id}")
    def case_detail(case_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
        detail = get_case_detail(session, case_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Caso no encontrado")
        return detail

    @app.get("/api/cases/{case_id}/history")
    def case_history(case_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
        case_row = RepairCaseRepo(session).get_by_id(case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Caso no encontrado")
        return get_case_history(session, case_id)

    @app.post("/api/cases/{case_id}/jobs/stage")
    def run_stage(case_id: str, payload: RunStageRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
        case_row = RepairCaseRepo(session).get_by_id(case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Caso no encontrado")

        try:
            job = enqueue_stage_job(
                session=session,
                case_id=case_id,
                stage=payload.stage,
                params=payload.params,
                requested_by=payload.requested_by,
            )
            session.commit()
            return {"job": serialize_job(job)}
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/cases/{case_id}/jobs/pipeline")
    def run_pipeline(case_id: str, payload: RunPipelineRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
        case_row = RepairCaseRepo(session).get_by_id(case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Caso no encontrado")

        try:
            job = enqueue_pipeline_job(
                session=session,
                case_id=case_id,
                start_from_stage=payload.start_from_stage,
                params_by_stage=payload.params_by_stage,
                requested_by=payload.requested_by,
            )
            session.commit()
            return {"job": serialize_job(job)}
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
        job = PipelineJobRepo(session).get_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        return {"job": serialize_job(job)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, payload: CancelJobRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
        _ = payload
        try:
            job = request_job_cancel(session, job_id)
            session.commit()
            return {"job": serialize_job(job)}
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}/logs")
    def job_logs(job_id: str, tail: int = Query(default=200, ge=20, le=2000), session: Session = Depends(get_db)) -> dict[str, Any]:
        job = PipelineJobRepo(session).get_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        lines = read_tail_lines(job.log_path, lines=tail)
        return {
            "job_id": job_id,
            "status": job.status,
            "current_stage": job.current_stage,
            "lines": lines,
        }

    @app.get("/api/jobs/{job_id}/stream")
    def job_stream(job_id: str):
        return StreamingResponse(
            _job_stream_generator(job_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.get("/api/stream/active")
    def stream_active_jobs():
        return StreamingResponse(
            _active_jobs_stream_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.get("/api/cases/{case_id}/artifact-content")
    def artifact_content(
        case_id: str,
        path: str,
        max_bytes: int = Query(default=100_000, ge=1_000, le=500_000),
        session: Session = Depends(get_db),
    ) -> dict[str, Any]:
        case_row = RepairCaseRepo(session).get_by_id(case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Caso no encontrado")

        resolved = _resolve_artifact_path(case_id, case_row.artifact_dir, path)
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Artifact no encontrado")

        content = resolved.read_text(encoding="utf-8", errors="replace")
        truncated = False
        if len(content.encode("utf-8")) > max_bytes:
            encoded = content.encode("utf-8")[:max_bytes]
            content = encoded.decode("utf-8", errors="replace") + "\n... [truncated]"
            truncated = True

        return {
            "case_id": case_id,
            "path": str(resolved),
            "content": content,
            "truncated": truncated,
        }

    @app.get("/api/reports/compare")
    def compare_reports(
        modes: str | None = Query(default=None),
        case_id: str | None = Query(default=None),
        session: Session = Depends(get_db),
    ) -> dict[str, Any]:
        mode_filter = [m.strip() for m in modes.split(",")] if modes else None

        stmt = select(EvaluationMetric)
        if case_id:
            stmt = stmt.where(EvaluationMetric.repair_case_id == case_id)
        if mode_filter:
            stmt = stmt.where(EvaluationMetric.repair_mode.in_(mode_filter))

        rows = list(session.scalars(stmt).all())
        grouped: dict[str, list[EvaluationMetric]] = {}
        for row in rows:
            grouped.setdefault(row.repair_mode, []).append(row)

        def _avg(vals: list[float | None]) -> float | None:
            nums = [v for v in vals if v is not None]
            if not nums:
                return None
            return round(sum(nums) / len(nums), 4)

        comparison = []
        for mode, metrics in sorted(grouped.items()):
            comparison.append(
                {
                    "repair_mode": mode,
                    "cases": len(metrics),
                    "bsr": _avg([m.bsr for m in metrics]),
                    "ctsr": _avg([m.ctsr for m in metrics]),
                    "ffsr": _avg([m.ffsr for m in metrics]),
                    "efr": _avg([m.efr for m in metrics]),
                    "hit_at_1": _avg([m.hit_at_1 for m in metrics]),
                    "hit_at_3": _avg([m.hit_at_3 for m in metrics]),
                    "hit_at_5": _avg([m.hit_at_5 for m in metrics]),
                    "source_set_accuracy": _avg([m.source_set_accuracy for m in metrics]),
                }
            )

        return {"comparison": comparison, "rows": len(rows)}

    return app


def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _job_stream_generator(job_id: str):
    factory = get_session_factory()
    last_state: tuple[str | None, str | None, str | None] = (None, None, None)
    log_offset = 0

    while True:
        session = factory()
        try:
            job = PipelineJobRepo(session).get_by_id(job_id)
            if job is None:
                yield _sse("error", {"message": "job no encontrado"})
                return

            state = (job.status, job.current_stage, job.error_message)
            if state != last_state:
                yield _sse("status", serialize_job(job))
                last_state = state

            if job.log_path:
                p = Path(job.log_path)
                if p.exists() and p.is_file():
                    with p.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(log_offset)
                        chunk = handle.read()
                        log_offset = handle.tell()
                    if chunk:
                        for line in chunk.splitlines():
                            yield _sse("log", {"line": line})

            if job.status in ("SUCCEEDED", "FAILED", "CANCELED"):
                yield _sse("done", serialize_job(job))
                return

            yield _sse("heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})
        finally:
            session.close()

        time.sleep(1.0)


def _active_jobs_stream_generator():
    factory = get_session_factory()
    last_payload: str | None = None
    while True:
        session = factory()
        try:
            active = list_active_jobs(session)
            payload = json.dumps(active, default=str, sort_keys=True)
            if payload != last_payload:
                last_payload = payload
                yield _sse("active", active)
            else:
                yield _sse("heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})
        finally:
            session.close()

        time.sleep(1.5)


def _resolve_artifact_path(case_id: str, artifact_dir: str | None, requested_path: str) -> Path:
    settings = get_settings()
    root = Path(artifact_dir).resolve() if artifact_dir else (settings.artifact_base / case_id).resolve()
    candidate = Path(requested_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()

    if not str(candidate).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Ruta fuera del directorio de artifacts del caso")

    return candidate


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "kmp_repair_webapi.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
