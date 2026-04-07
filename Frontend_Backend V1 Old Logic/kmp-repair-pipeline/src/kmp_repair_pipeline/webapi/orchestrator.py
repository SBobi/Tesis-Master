"""Stage orchestrator reused by web worker jobs."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from ..baselines.baseline_runner import run_all_baselines
from ..case_builder.case_factory import build_case
from ..evaluation.evaluator import evaluate
from ..explanation.explainer import explain
from ..localization.localizer import localize
from ..repair.repairer import repair
from ..reporting.reporter import generate_report
from ..runners.execution_runner import run_before_after
from ..static_analysis.structural_builder import analyze_case
from ..storage.repositories import CaseStatusTransitionRepo, RepairCaseRepo
from ..validation.validator import validate
from ..utils.llm_provider import get_default_provider
from .stages import CASE_RUNNABLE_STAGES


@dataclass
class StageResult:
    stage: str
    duration_s: float
    case_status: str
    summary: dict


def pipeline_plan(start_from_stage: str) -> list[str]:
    if start_from_stage not in CASE_RUNNABLE_STAGES:
        raise ValueError(f"Etapa inválida: {start_from_stage}")
    start_idx = CASE_RUNNABLE_STAGES.index(start_from_stage)
    return CASE_RUNNABLE_STAGES[start_idx:]


def run_stage_with_audit(
    *,
    session: Session,
    case_id: str,
    stage: str,
    params: dict,
    pipeline_job_id: str,
    log: Callable[[str], None],
) -> StageResult:
    """Run one stage and persist transition history with metadata."""
    case_repo = RepairCaseRepo(session)
    transition_repo = CaseStatusTransitionRepo(session)

    case_row = case_repo.get_by_id(case_id)
    if case_row is None:
        raise ValueError(f"Case {case_id} no existe")

    started_status = case_row.status
    start = time.perf_counter()

    transition_repo.create(
        repair_case_id=case_id,
        pipeline_job_id=pipeline_job_id,
        stage=stage,
        from_status=started_status,
        to_status=started_status,
        transition_type="STAGE_STARTED",
        metadata_json={"params": params},
        message=f"Inicio de etapa {stage}",
    )
    session.flush()

    log(f"[{stage}] inicio")

    try:
        summary = _run_stage_impl(session=session, case_id=case_id, stage=stage, params=params)
        session.flush()

        case_row = case_repo.get_by_id(case_id)
        ended_status = case_row.status if case_row else "FAILED"
        duration_s = round(time.perf_counter() - start, 3)

        transition_repo.create(
            repair_case_id=case_id,
            pipeline_job_id=pipeline_job_id,
            stage=stage,
            from_status=started_status,
            to_status=ended_status,
            transition_type="STAGE_COMPLETED",
            metadata_json={"duration_s": duration_s, "summary": summary, "params": params},
            message=f"Etapa {stage} completada",
        )
        log(f"[{stage}] completada en {duration_s:.3f}s")

        return StageResult(
            stage=stage,
            duration_s=duration_s,
            case_status=ended_status,
            summary=summary,
        )
    except Exception as exc:
        duration_s = round(time.perf_counter() - start, 3)

        case_row = case_repo.get_by_id(case_id)
        failure_from_status = case_row.status if case_row else started_status
        if case_row is not None:
            case_repo.set_status(case_row, "FAILED")

        transition_repo.create(
            repair_case_id=case_id,
            pipeline_job_id=pipeline_job_id,
            stage=stage,
            from_status=failure_from_status,
            to_status="FAILED",
            transition_type="STAGE_FAILED",
            metadata_json={"duration_s": duration_s, "params": params},
            message=str(exc),
        )
        log(f"[{stage}] ERROR: {exc}")
        raise


def _provider(provider_name: str | None, model: str | None):
    if provider_name or model:
        return get_default_provider(provider_name=provider_name, model_id=model)
    return None


def _summary_dict(value: object) -> dict:
    """Serialize stage result objects to plain dicts for job summaries."""
    if isinstance(value, dict):
        return value

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()

    if is_dataclass(value):
        return asdict(value)

    raise TypeError(f"No se pudo serializar {type(value).__name__} en summary")


def _run_stage_impl(session: Session, case_id: str, stage: str, params: dict) -> dict:
    if stage == "build-case":
        result = build_case(
            case_id=case_id,
            session=session,
            artifact_base=Path(params["artifact_base"]),
            work_base=Path(params["work_dir"]) if params.get("work_dir") else None,
            overwrite_clone=params.get("overwrite", False),
        )
        return {
            "already_built": result.already_built,
            "before_path": str(result.before_path),
            "after_path": str(result.after_path),
            "artifact_dir": str(result.artifact_dir),
        }

    if stage == "run-before-after":
        result = run_before_after(
            case_id=case_id,
            session=session,
            artifact_base=Path(params["artifact_base"]),
            targets=params.get("targets"),
            timeout_s=params["timeout_s"],
        )
        return {
            "ran_revisions": result.ran_revisions,
            "runnable_targets": result.env_profile.runnable_targets,
            "unavailable_targets": result.env_profile.unavailable_targets,
            "total_errors": result.total_errors,
        }

    if stage == "analyze-case":
        result = analyze_case(case_id=case_id, session=session)
        return {
            "total_kotlin_files": result.total_kotlin_files,
            "total_impacted_files": result.total_impacted_files,
            "impact_graphs": len(result.impact_graphs),
        }

    if stage == "localize":
        provider_impl = _provider(params.get("provider"), params.get("model"))
        result = localize(
            case_id=case_id,
            session=session,
            artifact_base=Path(params["artifact_base"]),
            use_agent=not params["no_agent"],
            provider=provider_impl,
            top_k=params["top_k"],
        )
        return {
            "used_agent": result.used_agent,
            "agent_notes": result.agent_notes,
            "total_candidates": result.total_candidates,
        }

    if stage == "repair":
        provider_impl = _provider(params.get("provider"), params.get("model"))
        result = repair(
            case_id=case_id,
            session=session,
            artifact_base=Path(params["artifact_base"]),
            repair_mode=params["mode"],
            provider=provider_impl,
            top_k=params["top_k"],
            patch_strategy=params["patch_strategy"],
            force_patch_attempt=params["force_patch_attempt"],
        )
        return {
            "attempt_number": result.attempt_number,
            "repair_mode": result.repair_mode,
            "patch_strategy": result.patch_strategy,
            "patch_status": result.patch_status,
            "diff_path": result.diff_path,
            "touched_files": result.touched_files,
        }

    if stage == "validate":
        result = validate(
            case_id=case_id,
            session=session,
            artifact_base=Path(params["artifact_base"]),
            patch_attempt_id=params.get("attempt_id"),
            targets=params.get("targets"),
            timeout_s=params["timeout_s"],
        )
        return {
            "patch_status": result.patch_status,
            "overall_status": result.overall_status,
            "targets": [
                {"target": r.target, "status": r.status.value, "unavailable_reason": r.unavailable_reason}
                for r in result.target_results
            ],
        }

    if stage == "explain":
        provider_impl = _provider(params.get("provider"), params.get("model"))
        result = explain(
            case_id=case_id,
            session=session,
            artifact_base=Path(params["artifact_base"]),
            provider=provider_impl,
        )
        return {
            "json_path": result.json_path,
            "markdown_path": result.markdown_path,
            "model_id": result.model_id,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        }

    if stage == "metrics":
        result = evaluate(
            case_id=case_id,
            session=session,
            ground_truth=params.get("ground_truth"),
        )
        return {
            "metrics": [_summary_dict(m) for m in result.metrics],
            "metric_count": len(result.metrics),
        }

    if stage == "report":
        cases = params.get("cases") or [case_id]
        result = generate_report(
            session=session,
            output_dir=Path(params["output_dir"]),
            formats=(params["format"],),
            repair_modes=params.get("modes"),
            case_ids=cases,
        )
        return {
            "row_count": result.row_count,
            "files": result.files,
            "aggregates": result.aggregates,
        }

    raise ValueError(f"Etapa no soportada: {stage}")
