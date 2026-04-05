"""Data access layer — typed repository classes for each aggregate root.

No raw SQL lives in business logic. All DB access goes through these
repository classes, which accept a SQLAlchemy Session.

Each repository:
  - Has a Session injected at construction time.
  - Exposes typed read/write methods.
  - Never commits; the caller controls the transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AgentLog,
    DependencyDiff,
    DependencyEvent,
    ErrorObservation,
    EvaluationMetric,
    ExecutionRun,
    ExpectActualLink,
    Explanation,
    LocalizationCandidate,
    PatchAttempt,
    RepairCase,
    Repository,
    Revision,
    SourceEntity,
    TaskResult,
    ValidationRun,
)


# ---------------------------------------------------------------------------
# RepositoryRepo
# ---------------------------------------------------------------------------


class RepositoryRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_or_create(self, url: str) -> Repository:
        stmt = select(Repository).where(Repository.url == url)
        existing = self._s.scalars(stmt).first()
        if existing:
            return existing
        repo = Repository(url=url)
        self._s.add(repo)
        self._s.flush()
        return repo

    def get_by_id(self, id: str) -> Optional[Repository]:
        return self._s.get(Repository, id)

    def list_all(self) -> list[Repository]:
        return list(self._s.scalars(select(Repository).order_by(Repository.created_at)).all())


# ---------------------------------------------------------------------------
# DependencyEventRepo
# ---------------------------------------------------------------------------


class DependencyEventRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repository_id: str,
        update_class: str,
        pr_ref: str | None = None,
        raw_diff: str | None = None,
        source: str = "manual",
    ) -> DependencyEvent:
        event = DependencyEvent(
            repository_id=repository_id,
            update_class=update_class,
            pr_ref=pr_ref,
            raw_diff=raw_diff,
            source=source,
        )
        self._s.add(event)
        self._s.flush()
        return event

    def get_by_id(self, id: str) -> Optional[DependencyEvent]:
        return self._s.get(DependencyEvent, id)

    def list_for_repo(self, repository_id: str) -> list[DependencyEvent]:
        stmt = select(DependencyEvent).where(
            DependencyEvent.repository_id == repository_id
        ).order_by(DependencyEvent.created_at.desc())
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# DependencyDiffRepo
# ---------------------------------------------------------------------------


class DependencyDiffRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        dependency_event_id: str,
        dependency_group: str,
        version_before: str,
        version_after: str,
        version_key: str | None = None,
    ) -> DependencyDiff:
        diff = DependencyDiff(
            dependency_event_id=dependency_event_id,
            dependency_group=dependency_group,
            version_before=version_before,
            version_after=version_after,
            version_key=version_key,
        )
        self._s.add(diff)
        self._s.flush()
        return diff

    def list_for_event(self, dependency_event_id: str) -> list[DependencyDiff]:
        stmt = select(DependencyDiff).where(
            DependencyDiff.dependency_event_id == dependency_event_id
        )
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# RepairCaseRepo
# ---------------------------------------------------------------------------


class RepairCaseRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(self, dependency_event_id: str, artifact_dir: str | None = None) -> RepairCase:
        case = RepairCase(dependency_event_id=dependency_event_id, artifact_dir=artifact_dir)
        self._s.add(case)
        self._s.flush()
        return case

    def get_by_id(self, id: str) -> Optional[RepairCase]:
        return self._s.get(RepairCase, id)

    def set_status(self, case: RepairCase, status: str) -> None:
        case.status = status
        case.updated_at = datetime.now(timezone.utc)
        self._s.flush()

    def list_all(self) -> list[RepairCase]:
        return list(self._s.scalars(select(RepairCase).order_by(RepairCase.created_at.desc())).all())


# ---------------------------------------------------------------------------
# RevisionRepo
# ---------------------------------------------------------------------------


class RevisionRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        revision_type: str,
        git_sha: str | None = None,
        local_path: str | None = None,
        manifest_path: str | None = None,
        manifest_sha256: str | None = None,
    ) -> Revision:
        rev = Revision(
            repair_case_id=repair_case_id,
            revision_type=revision_type,
            git_sha=git_sha,
            local_path=local_path,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
        )
        self._s.add(rev)
        self._s.flush()
        return rev

    def get(self, repair_case_id: str, revision_type: str) -> Optional[Revision]:
        stmt = select(Revision).where(
            Revision.repair_case_id == repair_case_id,
            Revision.revision_type == revision_type,
        )
        return self._s.scalars(stmt).first()


# ---------------------------------------------------------------------------
# ExecutionRunRepo
# ---------------------------------------------------------------------------


class ExecutionRunRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        revision_type: str,
        profile: str = "linux-fast",
        env_metadata: dict | None = None,
    ) -> ExecutionRun:
        run = ExecutionRun(
            repair_case_id=repair_case_id,
            revision_type=revision_type,
            profile=profile,
            env_metadata=env_metadata,
        )
        self._s.add(run)
        self._s.flush()
        return run

    def get_by_id(self, id: str) -> Optional[ExecutionRun]:
        return self._s.get(ExecutionRun, id)

    def list_for_case(self, repair_case_id: str) -> list[ExecutionRun]:
        stmt = select(ExecutionRun).where(
            ExecutionRun.repair_case_id == repair_case_id
        ).order_by(ExecutionRun.created_at)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# TaskResultRepo
# ---------------------------------------------------------------------------


class TaskResultRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        execution_run_id: str,
        task_name: str,
        exit_code: int | None = None,
        status: str = "NOT_RUN_YET",
        duration_s: float | None = None,
        stdout_path: str | None = None,
        stdout_sha256: str | None = None,
        stderr_path: str | None = None,
        stderr_sha256: str | None = None,
    ) -> TaskResult:
        result = TaskResult(
            execution_run_id=execution_run_id,
            task_name=task_name,
            exit_code=exit_code,
            status=status,
            duration_s=duration_s,
            stdout_path=stdout_path,
            stdout_sha256=stdout_sha256,
            stderr_path=stderr_path,
            stderr_sha256=stderr_sha256,
        )
        self._s.add(result)
        self._s.flush()
        return result

    def list_for_run(self, execution_run_id: str) -> list[TaskResult]:
        stmt = select(TaskResult).where(TaskResult.execution_run_id == execution_run_id)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# ErrorObservationRepo
# ---------------------------------------------------------------------------


class ErrorObservationRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        task_result_id: str,
        error_type: str = "COMPILE_ERROR",
        file_path: str | None = None,
        line: int | None = None,
        column: int | None = None,
        message: str | None = None,
        raw_text: str | None = None,
        parser: str = "regex",
    ) -> ErrorObservation:
        obs = ErrorObservation(
            task_result_id=task_result_id,
            error_type=error_type,
            file_path=file_path,
            line=line,
            column=column,
            message=message,
            raw_text=raw_text,
            parser=parser,
        )
        self._s.add(obs)
        self._s.flush()
        return obs

    def list_for_task(self, task_result_id: str) -> list[ErrorObservation]:
        stmt = select(ErrorObservation).where(ErrorObservation.task_result_id == task_result_id)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# SourceEntityRepo
# ---------------------------------------------------------------------------


class SourceEntityRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        file_path: str,
        source_set: str = "common",
        package: str | None = None,
        declaration_kind: str | None = None,
        fqcn: str | None = None,
        is_expect: bool = False,
        is_actual: bool = False,
    ) -> SourceEntity:
        entity = SourceEntity(
            repair_case_id=repair_case_id,
            file_path=file_path,
            source_set=source_set,
            package=package,
            declaration_kind=declaration_kind,
            fqcn=fqcn,
            is_expect=is_expect,
            is_actual=is_actual,
        )
        self._s.add(entity)
        self._s.flush()
        return entity

    def list_for_case(self, repair_case_id: str) -> list[SourceEntity]:
        stmt = select(SourceEntity).where(SourceEntity.repair_case_id == repair_case_id)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# LocalizationCandidateRepo
# ---------------------------------------------------------------------------


class LocalizationCandidateRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        rank: int,
        score: float,
        classification: str,
        file_path: str | None = None,
        source_set: str | None = None,
        source_entity_id: str | None = None,
        score_breakdown: dict | None = None,
        localization_run: str | None = None,
    ) -> LocalizationCandidate:
        candidate = LocalizationCandidate(
            repair_case_id=repair_case_id,
            rank=rank,
            score=score,
            classification=classification,
            file_path=file_path,
            source_set=source_set,
            source_entity_id=source_entity_id,
            score_breakdown=score_breakdown,
            localization_run=localization_run,
        )
        self._s.add(candidate)
        self._s.flush()
        return candidate

    def list_for_case_ranked(self, repair_case_id: str) -> list[LocalizationCandidate]:
        stmt = select(LocalizationCandidate).where(
            LocalizationCandidate.repair_case_id == repair_case_id
        ).order_by(LocalizationCandidate.rank)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# PatchAttemptRepo
# ---------------------------------------------------------------------------


class PatchAttemptRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        attempt_number: int,
        repair_mode: str,
        model_id: str | None = None,
    ) -> PatchAttempt:
        attempt = PatchAttempt(
            repair_case_id=repair_case_id,
            attempt_number=attempt_number,
            repair_mode=repair_mode,
            model_id=model_id,
        )
        self._s.add(attempt)
        self._s.flush()
        return attempt

    def get_by_id(self, id: str) -> Optional[PatchAttempt]:
        return self._s.get(PatchAttempt, id)

    def list_for_case(self, repair_case_id: str) -> list[PatchAttempt]:
        stmt = select(PatchAttempt).where(
            PatchAttempt.repair_case_id == repair_case_id
        ).order_by(PatchAttempt.attempt_number)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# ValidationRunRepo
# ---------------------------------------------------------------------------


class ValidationRunRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        patch_attempt_id: str,
        target: str,
        status: str,
        unavailable_reason: str | None = None,
        execution_run_id: str | None = None,
    ) -> ValidationRun:
        run = ValidationRun(
            repair_case_id=repair_case_id,
            patch_attempt_id=patch_attempt_id,
            target=target,
            status=status,
            unavailable_reason=unavailable_reason,
            execution_run_id=execution_run_id,
        )
        self._s.add(run)
        self._s.flush()
        return run

    def list_for_patch(self, patch_attempt_id: str) -> list[ValidationRun]:
        stmt = select(ValidationRun).where(ValidationRun.patch_attempt_id == patch_attempt_id)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# AgentLogRepo
# ---------------------------------------------------------------------------


class AgentLogRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        agent_type: str,
        call_index: int = 0,
        model_id: str | None = None,
        prompt_path: str | None = None,
        prompt_sha256: str | None = None,
        response_path: str | None = None,
        response_sha256: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        latency_s: float | None = None,
        error: str | None = None,
    ) -> AgentLog:
        log_entry = AgentLog(
            repair_case_id=repair_case_id,
            agent_type=agent_type,
            call_index=call_index,
            model_id=model_id,
            prompt_path=prompt_path,
            prompt_sha256=prompt_sha256,
            response_path=response_path,
            response_sha256=response_sha256,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_s=latency_s,
            error=error,
        )
        self._s.add(log_entry)
        self._s.flush()
        return log_entry

    def list_for_case(self, repair_case_id: str) -> list[AgentLog]:
        stmt = select(AgentLog).where(
            AgentLog.repair_case_id == repair_case_id
        ).order_by(AgentLog.created_at)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# ExplanationRepo
# ---------------------------------------------------------------------------


class ExplanationRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        repair_case_id: str,
        patch_attempt_id: str | None = None,
        json_path: str | None = None,
        json_sha256: str | None = None,
        markdown_path: str | None = None,
        markdown_sha256: str | None = None,
        model_id: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> Explanation:
        row = Explanation(
            repair_case_id=repair_case_id,
            patch_attempt_id=patch_attempt_id,
            json_path=json_path,
            json_sha256=json_sha256,
            markdown_path=markdown_path,
            markdown_sha256=markdown_sha256,
            model_id=model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get_for_case(self, repair_case_id: str) -> list[Explanation]:
        stmt = select(Explanation).where(
            Explanation.repair_case_id == repair_case_id
        ).order_by(Explanation.created_at)
        return list(self._s.scalars(stmt).all())


# ---------------------------------------------------------------------------
# EvaluationMetricRepo
# ---------------------------------------------------------------------------


class EvaluationMetricRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(
        self,
        repair_case_id: str,
        repair_mode: str,
        **kwargs,
    ) -> EvaluationMetric:
        stmt = select(EvaluationMetric).where(
            EvaluationMetric.repair_case_id == repair_case_id,
            EvaluationMetric.repair_mode == repair_mode,
        )
        existing = self._s.scalars(stmt).first()
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.updated_at = datetime.now(timezone.utc)
            self._s.flush()
            return existing
        metric = EvaluationMetric(
            repair_case_id=repair_case_id,
            repair_mode=repair_mode,
            **kwargs,
        )
        self._s.add(metric)
        self._s.flush()
        return metric

    def list_for_case(self, repair_case_id: str) -> list[EvaluationMetric]:
        stmt = select(EvaluationMetric).where(EvaluationMetric.repair_case_id == repair_case_id)
        return list(self._s.scalars(stmt).all())

    def list_all(self, repair_modes: list[str] | None = None) -> list[EvaluationMetric]:
        stmt = select(EvaluationMetric).order_by(
            EvaluationMetric.repair_case_id, EvaluationMetric.repair_mode
        )
        if repair_modes:
            stmt = stmt.where(EvaluationMetric.repair_mode.in_(repair_modes))
        return list(self._s.scalars(stmt).all())
