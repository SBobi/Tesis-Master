"""Typed evidence sections for the Case Bundle.

Each section maps 1-to-1 with what the pipeline stages produce and what
agents consume. Sections are populated incrementally — a bundle may have
UpdateEvidence without yet having RepairEvidence.

All evidence objects are Pydantic models so they can be serialized to JSON
and their schema is explicit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from ..domain.analysis import ExpectActualPair, FileImpact, FileParseResult, ImpactGraph
from ..domain.events import DependencyUpdateEvent, UpdateClass, VersionChange
from ..domain.validation import UIRegressions, ValidationStatus


# ---------------------------------------------------------------------------
# Section 1 — Update Evidence (Stage 1 output)
# ---------------------------------------------------------------------------


class UpdateEvidence(BaseModel):
    """What was updated, how it was classified, raw evidence preserved."""
    update_event: DependencyUpdateEvent
    version_changes: list[VersionChange] = Field(default_factory=list)
    update_class: UpdateClass = UpdateClass.UNKNOWN
    build_file_diff: str = ""         # raw unified diff of build files
    # Auxiliary only — GitHub dep graph and SBOM are NOT primary source of truth
    github_dep_graph_path: Optional[str] = None
    sbom_path: Optional[str] = None
    # Provenance
    detected_at: Optional[datetime] = None
    detection_source: str = "manual"   # "dependabot_pr" | "toml_diff" | "manual"
    # Catalog diff — populated when before/after libs.versions.toml are available.
    # alias_renames: list of {before_alias, after_alias, module} — alias was renamed
    # artifact_renames: list of {alias, before_module, after_module} — artifact changed
    catalog_alias_diff: dict = Field(default_factory=dict)
    artifact_renames: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Section 2 — Execution Evidence (Stage 2 output)
# ---------------------------------------------------------------------------


class TaskOutcome(BaseModel):
    """Result of one Gradle task execution."""
    task_name: str
    exit_code: Optional[int] = None
    status: ValidationStatus = ValidationStatus.NOT_RUN_YET
    duration_s: Optional[float] = None
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    stdout_sha256: Optional[str] = None
    stderr_sha256: Optional[str] = None


class ErrorObservation(BaseModel):
    """One parsed error from a task's output."""
    error_type: str = "COMPILE_ERROR"
    file_path: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    message: Optional[str] = None
    raw_text: Optional[str] = None
    parser: str = "regex"
    # Populated for KLIB_ABI_ERROR when the compiler w: warning line reveals the
    # exact Kotlin version that produced the incompatible KLIB.  RepairAgent uses
    # this to emit the correct target version rather than guessing from context.
    required_kotlin_version: Optional[str] = None
    # Populated for API_BREAK_ERROR: the unresolved symbol name extracted from
    # the "Unresolved reference: Foo" or "Type mismatch: inferred type is Foo"
    # compiler message.  Lets the RepairAgent identify the exact API that changed.
    symbol_name: Optional[str] = None


class RevisionExecution(BaseModel):
    """Execution results for one revision (before, after, or patched)."""
    revision_type: str              # "before" | "after" | "patched"
    profile: str = "linux-fast"
    overall_status: ValidationStatus = ValidationStatus.NOT_RUN_YET
    task_outcomes: list[TaskOutcome] = Field(default_factory=list)
    error_observations: list[ErrorObservation] = Field(default_factory=list)
    env_metadata: dict = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class ExecutionEvidence(BaseModel):
    """Before and after execution results."""
    before: Optional[RevisionExecution] = None
    after: Optional[RevisionExecution] = None

    def failing_tasks(self, revision: str = "after") -> list[TaskOutcome]:
        rev = self.before if revision == "before" else self.after
        if rev is None:
            return []
        return [t for t in rev.task_outcomes if t.exit_code not in (None, 0)]

    def all_errors(self, revision: str = "after") -> list[ErrorObservation]:
        rev = self.before if revision == "before" else self.after
        if rev is None:
            return []
        return rev.error_observations


# ---------------------------------------------------------------------------
# Section 3 — Structural Evidence (Stage 2/3 output)
# ---------------------------------------------------------------------------


class SourceSetMap(BaseModel):
    """KMP source-set membership for files in the repository."""
    common_files: list[str] = Field(default_factory=list)
    android_files: list[str] = Field(default_factory=list)
    ios_files: list[str] = Field(default_factory=list)
    jvm_files: list[str] = Field(default_factory=list)
    other: dict[str, list[str]] = Field(default_factory=dict)

    def source_set_for(self, file_path: str) -> str:
        if file_path in self.common_files:
            return "common"
        if file_path in self.android_files:
            return "android"
        if file_path in self.ios_files:
            return "ios"
        if file_path in self.jvm_files:
            return "jvm"
        for ss, files in self.other.items():
            if file_path in files:
                return ss
        return "unknown"


class StructuralEvidence(BaseModel):
    """KMP-aware structural analysis of the repository."""
    impact_graph: Optional[ImpactGraph] = None
    source_set_map: SourceSetMap = Field(default_factory=SourceSetMap)
    expect_actual_pairs: list[ExpectActualPair] = Field(default_factory=list)
    # Import-level evidence: which files import from the changed dependency
    direct_import_files: list[str] = Field(default_factory=list)
    # Build-level configuration files relevant to the update
    relevant_build_files: list[str] = Field(default_factory=list)
    # Parsed version catalog from gradle/libs.versions.toml (key → version string).
    # Populated by structural_builder; used by RepairAgent for version-bump fixes.
    version_catalog: dict[str, str] = Field(default_factory=dict)
    total_kotlin_files: int = 0


# ---------------------------------------------------------------------------
# Section 4 — Repair Evidence (Stage 3/4 output)
# ---------------------------------------------------------------------------


class LocalizationResult(BaseModel):
    """Ranked localization candidates from the LocalizationAgent."""

    class Candidate(BaseModel):
        rank: int
        file_path: str
        source_set: str = "common"
        classification: str = "uncertain"
        # "shared_code" | "platform_specific" | "build_level" | "uncertain"
        score: float = 0.0
        score_breakdown: dict = Field(default_factory=dict)

    candidates: list[Candidate] = Field(default_factory=list)
    localization_run: str = ""       # identifier for reproducibility
    agent_prompt_path: Optional[str] = None
    agent_response_path: Optional[str] = None

    def top_k(self, k: int) -> list[Candidate]:
        return sorted(self.candidates, key=lambda c: c.rank)[:k]

    def files_at_rank(self, k: int) -> list[str]:
        return [c.file_path for c in self.top_k(k)]


class PatchAttempt(BaseModel):
    """One patch synthesis attempt from the RepairAgent."""
    attempt_number: int
    repair_mode: str
    # "full_thesis" | "raw_error" | "context_rich" | "iterative_agentic"
    status: str = "PENDING"
    diff_text: str = ""
    diff_path: Optional[str] = None
    diff_sha256: Optional[str] = None
    touched_files: list[str] = Field(default_factory=list)
    prompt_path: Optional[str] = None
    prompt_sha256: Optional[str] = None
    response_path: Optional[str] = None
    response_sha256: Optional[str] = None
    model_id: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    retry_reason: Optional[str] = None


class RepairEvidence(BaseModel):
    """Localization and patch synthesis results."""
    localization: Optional[LocalizationResult] = None
    patch_attempts: list[PatchAttempt] = Field(default_factory=list)

    def latest_patch(self) -> Optional[PatchAttempt]:
        """Return the most recently created patch attempt.

        Prefers a patch that was validated (REJECTED or VALIDATED status),
        since that's the most informative for the ExplanationAgent.
        Falls back to the last attempt in list order (ordered by created_at).
        """
        if not self.patch_attempts:
            return None
        # Prefer validated/rejected (has validation signal) over failed-apply
        for p in reversed(self.patch_attempts):
            if p.status in ("VALIDATED", "REJECTED", "APPLIED"):
                return p
        return self.patch_attempts[-1]

    def accepted_patch(self) -> Optional[PatchAttempt]:
        for p in reversed(self.patch_attempts):
            if p.status in ("VALIDATED", "APPLIED"):
                return p
        return None


# ---------------------------------------------------------------------------
# Section 5 — Validation Evidence (Stage 5 output)
# ---------------------------------------------------------------------------


class TargetValidation(BaseModel):
    """Validation result for one target after patch application."""
    target: str                          # "shared" | "android" | "ios" | "repository_level"
    status: ValidationStatus = ValidationStatus.NOT_RUN_YET
    unavailable_reason: Optional[str] = None
    patch_attempt_number: int = 1
    duration_s: Optional[float] = None
    task_outcomes: list[TaskOutcome] = Field(default_factory=list)
    error_observations: list[ErrorObservation] = Field(default_factory=list)


class ValidationEvidence(BaseModel):
    """Multi-target validation results for one patch attempt."""
    target_results: list[TargetValidation] = Field(default_factory=list)
    repository_level_status: ValidationStatus = ValidationStatus.NOT_RUN_YET

    def result_for(self, target: str) -> Optional[TargetValidation]:
        return next((r for r in self.target_results if r.target == target), None)

    def all_required_passed(self, required_targets: list[str]) -> bool:
        for t in required_targets:
            r = self.result_for(t)
            if r is None or r.status != ValidationStatus.SUCCESS_REPOSITORY_LEVEL:
                return False
        return True

    def has_unavailable_target(self) -> bool:
        return any(
            r.status == ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE
            for r in self.target_results
        )


# ---------------------------------------------------------------------------
# Section 6 — Explanation Evidence (Stage 5 output)
# ---------------------------------------------------------------------------


class ExplanationEvidence(BaseModel):
    """Structured and narrative explanation artifacts."""

    class Uncertainty(BaseModel):
        kind: str             # "environment" | "localization" | "patch" | "validation"
        description: str

    what_was_updated: str = ""
    update_class_rationale: str = ""
    localization_summary: str = ""
    patch_rationale: str = ""
    validation_summary: str = ""
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    target_coverage_complete: bool = False
    # Artifact paths
    json_path: Optional[str] = None
    json_sha256: Optional[str] = None
    markdown_path: Optional[str] = None
    markdown_sha256: Optional[str] = None
    # Provenance
    model_id: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
