"""SQLAlchemy ORM table definitions for the kmp-repair-pipeline database.

Schema hierarchy:
  repositories
  └── dependency_events
      └── repair_cases
          ├── revisions
          ├── dependency_diffs
          ├── execution_runs ── task_results ── error_observations
          ├── source_entities ── expect_actual_links
          ├── localization_candidates
          ├── patch_attempts ── validation_runs
          ├── explanations
          ├── agent_logs
          └── evaluation_metrics

Every table has created_at, updated_at.
Every artifact reference has a storage_path and sha256 for provenance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# repositories
# ---------------------------------------------------------------------------


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    stars: Mapped[int | None] = mapped_column(Integer)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Provenance
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    dependency_events: Mapped[list[DependencyEvent]] = relationship(back_populates="repository")


# ---------------------------------------------------------------------------
# dependency_events
# ---------------------------------------------------------------------------


class DependencyEvent(Base):
    __tablename__ = "dependency_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id"), nullable=False)
    pr_ref: Mapped[str | None] = mapped_column(String(255))
    update_class: Mapped[str] = mapped_column(String(64), nullable=False)  # UpdateClass enum value
    raw_diff: Mapped[str | None] = mapped_column(Text)
    raw_diff_path: Mapped[str | None] = mapped_column(Text)
    raw_diff_sha256: Mapped[str | None] = mapped_column(String(64))
    # Provenance
    source: Mapped[str] = mapped_column(String(64), default="manual")  # "dependabot_pr" | "toml_diff" | "manual"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    repository: Mapped[Repository] = relationship(back_populates="dependency_events")
    dependency_diffs: Mapped[list[DependencyDiff]] = relationship(back_populates="dependency_event")
    repair_cases: Mapped[list[RepairCase]] = relationship(back_populates="dependency_event")


# ---------------------------------------------------------------------------
# dependency_diffs
# ---------------------------------------------------------------------------


class DependencyDiff(Base):
    __tablename__ = "dependency_diffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dependency_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("dependency_events.id"), nullable=False)
    dependency_group: Mapped[str] = mapped_column(Text, nullable=False)
    version_key: Mapped[str | None] = mapped_column(String(255))
    version_before: Mapped[str] = mapped_column(String(128), nullable=False)
    version_after: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    dependency_event: Mapped[DependencyEvent] = relationship(back_populates="dependency_diffs")


# ---------------------------------------------------------------------------
# repair_cases
# ---------------------------------------------------------------------------


class RepairCase(Base):
    __tablename__ = "repair_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dependency_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("dependency_events.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="CREATED")
    # CREATED | SHADOW_BUILT | EXECUTED | LOCALIZED | PATCH_ATTEMPTED | VALIDATED | EXPLAINED | EVALUATED | FAILED
    artifact_dir: Mapped[str | None] = mapped_column(Text)  # local path under data/artifacts/<case_id>/
    # Provenance
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    dependency_event: Mapped[DependencyEvent] = relationship(back_populates="repair_cases")
    revisions: Mapped[list[Revision]] = relationship(back_populates="repair_case")
    execution_runs: Mapped[list[ExecutionRun]] = relationship(back_populates="repair_case")
    source_entities: Mapped[list[SourceEntity]] = relationship(back_populates="repair_case")
    localization_candidates: Mapped[list[LocalizationCandidate]] = relationship(back_populates="repair_case")
    patch_attempts: Mapped[list[PatchAttempt]] = relationship(back_populates="repair_case")
    explanations: Mapped[list[Explanation]] = relationship(back_populates="repair_case")
    agent_logs: Mapped[list[AgentLog]] = relationship(back_populates="repair_case")
    evaluation_metrics: Mapped[list[EvaluationMetric]] = relationship(back_populates="repair_case")


# ---------------------------------------------------------------------------
# revisions
# ---------------------------------------------------------------------------


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    revision_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "before" | "after" | "patched"
    git_sha: Mapped[str | None] = mapped_column(String(40))
    local_path: Mapped[str | None] = mapped_column(Text)
    manifest_path: Mapped[str | None] = mapped_column(Text)  # path to ShadowManifest JSON
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="revisions")

    __table_args__ = (UniqueConstraint("repair_case_id", "revision_type", name="uq_revision_type_per_case"),)


# ---------------------------------------------------------------------------
# execution_runs
# ---------------------------------------------------------------------------


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    revision_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "before" | "after" | "patched"
    profile: Mapped[str] = mapped_column(String(64), default="linux-fast")
    # "linux-fast" | "linux-android" | "macos-full"
    status: Mapped[str] = mapped_column(String(64), default="NOT_RUN_YET")
    # Uses ValidationStatus vocabulary
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    # Environment metadata — provenance
    env_metadata: Mapped[dict | None] = mapped_column(JSONB)
    # e.g. {"os": "linux", "java_version": "17", "gradle_version": "8.5"}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="execution_runs")
    task_results: Mapped[list[TaskResult]] = relationship(back_populates="execution_run")


# ---------------------------------------------------------------------------
# task_results
# ---------------------------------------------------------------------------


class TaskResult(Base):
    __tablename__ = "task_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("execution_runs.id"), nullable=False)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # e.g. ":compileCommonMainKotlinMetadata", ":testDebugUnitTest"
    exit_code: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(64), default="NOT_RUN_YET")
    duration_s: Mapped[float | None] = mapped_column(Float)
    # Artifact paths — large files stored on disk
    stdout_path: Mapped[str | None] = mapped_column(Text)
    stdout_sha256: Mapped[str | None] = mapped_column(String(64))
    stderr_path: Mapped[str | None] = mapped_column(Text)
    stderr_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    execution_run: Mapped[ExecutionRun] = relationship(back_populates="task_results")
    error_observations: Mapped[list[ErrorObservation]] = relationship(back_populates="task_result")


# ---------------------------------------------------------------------------
# error_observations
# ---------------------------------------------------------------------------


class ErrorObservation(Base):
    __tablename__ = "error_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_result_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_results.id"), nullable=False)
    error_type: Mapped[str] = mapped_column(String(64), default="COMPILE_ERROR")
    # "COMPILE_ERROR" | "TEST_FAILURE" | "LINK_ERROR" | "RUNTIME_ERROR" | "BUILD_SCRIPT_ERROR"
    file_path: Mapped[str | None] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer)
    column: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    # Provenance: which parser extracted this
    parser: Mapped[str] = mapped_column(String(64), default="regex")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task_result: Mapped[TaskResult] = relationship(back_populates="error_observations")


# ---------------------------------------------------------------------------
# source_entities
# ---------------------------------------------------------------------------


class SourceEntity(Base):
    __tablename__ = "source_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_set: Mapped[str] = mapped_column(String(64), default="common")
    package: Mapped[str | None] = mapped_column(Text)
    declaration_kind: Mapped[str | None] = mapped_column(String(32))
    fqcn: Mapped[str | None] = mapped_column(Text)
    is_expect: Mapped[bool] = mapped_column(Boolean, default=False)
    is_actual: Mapped[bool] = mapped_column(Boolean, default=False)
    # Provenance
    extracted_from: Mapped[str] = mapped_column(String(32), default="kotlin_parser")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="source_entities")
    expect_links: Mapped[list[ExpectActualLink]] = relationship(
        foreign_keys="ExpectActualLink.expect_entity_id",
        back_populates="expect_entity",
    )
    actual_links: Mapped[list[ExpectActualLink]] = relationship(
        foreign_keys="ExpectActualLink.actual_entity_id",
        back_populates="actual_entity",
    )
    localization_candidates: Mapped[list[LocalizationCandidate]] = relationship(back_populates="source_entity")


# ---------------------------------------------------------------------------
# expect_actual_links
# ---------------------------------------------------------------------------


class ExpectActualLink(Base):
    __tablename__ = "expect_actual_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    expect_entity_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_entities.id"), nullable=False)
    actual_entity_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_entities.id"), nullable=False)
    fqcn: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    expect_entity: Mapped[SourceEntity] = relationship(
        foreign_keys=[expect_entity_id],
        back_populates="expect_links",
    )
    actual_entity: Mapped[SourceEntity] = relationship(
        foreign_keys=[actual_entity_id],
        back_populates="actual_links",
    )


# ---------------------------------------------------------------------------
# localization_candidates
# ---------------------------------------------------------------------------


class LocalizationCandidate(Base):
    __tablename__ = "localization_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    source_entity_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("source_entities.id"))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    # e.g. {"static_score": 0.8, "dynamic_score": 0.6, "expect_actual_boost": 0.2}
    classification: Mapped[str] = mapped_column(String(64), default="uncertain")
    # "shared_code" | "platform_specific" | "build_level" | "uncertain"
    file_path: Mapped[str | None] = mapped_column(Text)
    source_set: Mapped[str | None] = mapped_column(String(64))
    # Provenance
    localization_run: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="localization_candidates")
    source_entity: Mapped[SourceEntity | None] = relationship(back_populates="localization_candidates")


# ---------------------------------------------------------------------------
# patch_attempts
# ---------------------------------------------------------------------------


class PatchAttempt(Base):
    __tablename__ = "patch_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    repair_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    # "full_thesis" | "raw_error" | "context_rich" | "iterative_agentic"
    status: Mapped[str] = mapped_column(String(64), default="PENDING")
    # "PENDING" | "APPLIED" | "FAILED_APPLY" | "VALIDATED" | "REJECTED"
    # Artifact paths
    diff_path: Mapped[str | None] = mapped_column(Text)
    diff_sha256: Mapped[str | None] = mapped_column(String(64))
    touched_files: Mapped[list | None] = mapped_column(JSONB)
    # Prompt/response audit
    prompt_path: Mapped[str | None] = mapped_column(Text)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    response_path: Mapped[str | None] = mapped_column(Text)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    # LLM metadata — provenance
    model_id: Mapped[str | None] = mapped_column(String(128))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    retry_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="patch_attempts")
    validation_runs: Mapped[list[ValidationRun]] = relationship(back_populates="patch_attempt")
    explanations: Mapped[list[Explanation]] = relationship(back_populates="patch_attempt")

    __table_args__ = (
        UniqueConstraint("repair_case_id", "attempt_number", "repair_mode", name="uq_patch_attempt"),
    )


# ---------------------------------------------------------------------------
# validation_runs
# ---------------------------------------------------------------------------


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    patch_attempt_id: Mapped[str] = mapped_column(String(36), ForeignKey("patch_attempts.id"), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    # "shared" | "android" | "ios" | "jvm" | "repository_level"
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    # Uses ValidationStatus vocabulary exactly
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    execution_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("execution_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    patch_attempt: Mapped[PatchAttempt] = relationship(back_populates="validation_runs")


# ---------------------------------------------------------------------------
# explanations
# ---------------------------------------------------------------------------


class Explanation(Base):
    __tablename__ = "explanations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    patch_attempt_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("patch_attempts.id"))
    # Artifact paths
    json_path: Mapped[str | None] = mapped_column(Text)
    json_sha256: Mapped[str | None] = mapped_column(String(64))
    markdown_path: Mapped[str | None] = mapped_column(Text)
    markdown_sha256: Mapped[str | None] = mapped_column(String(64))
    # LLM metadata — provenance
    model_id: Mapped[str | None] = mapped_column(String(128))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="explanations")
    patch_attempt: Mapped[PatchAttempt | None] = relationship(back_populates="explanations")


# ---------------------------------------------------------------------------
# agent_logs  (auditable record of every LLM call)
# ---------------------------------------------------------------------------


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # "LocalizationAgent" | "RepairAgent" | "ExplanationAgent"
    call_index: Mapped[int] = mapped_column(Integer, default=0)
    model_id: Mapped[str | None] = mapped_column(String(128))
    prompt_path: Mapped[str | None] = mapped_column(Text)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    response_path: Mapped[str | None] = mapped_column(Text)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_s: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="agent_logs")


# ---------------------------------------------------------------------------
# evaluation_metrics
# ---------------------------------------------------------------------------


class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repair_case_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_cases.id"), nullable=False)
    repair_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    # BSR / CTSR / FFSR / EFR (per-case fractions: 1.0 = success, 0.0 = fail)
    bsr: Mapped[float | None] = mapped_column(Float)
    ctsr: Mapped[float | None] = mapped_column(Float)
    ffsr: Mapped[float | None] = mapped_column(Float)
    efr: Mapped[float | None] = mapped_column(Float)
    # Localization Hit@k
    hit_at_1: Mapped[float | None] = mapped_column(Float)
    hit_at_3: Mapped[float | None] = mapped_column(Float)
    hit_at_5: Mapped[float | None] = mapped_column(Float)
    # Attribution accuracy
    source_set_accuracy: Mapped[float | None] = mapped_column(Float)
    # Extra fields for flexibility
    extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    repair_case: Mapped[RepairCase] = relationship(back_populates="evaluation_metrics")

    __table_args__ = (
        UniqueConstraint("repair_case_id", "repair_mode", name="uq_metric_per_case_mode"),
    )
