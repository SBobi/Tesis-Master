"""Canonical typed Case Bundle — the primary runtime state of one repair case.

Design principles:
  - Every stage reads from and writes to the Case Bundle via typed accessors.
  - The bundle does NOT hold I/O or DB references; it is a pure data container.
  - Agents do not receive the whole bundle; they receive the specific evidence
    sections they need (enforced by the orchestrator in later phases).
  - The bundle is serializable to JSON and rehydratable from DB records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .evidence import (
    ErrorObservation,
    ExecutionEvidence,
    ExplanationEvidence,
    LocalizationResult,
    PatchAttempt,
    RepairEvidence,
    StructuralEvidence,
    UpdateEvidence,
    ValidationEvidence,
)


class CaseMeta(BaseModel):
    """Identifiers and provenance for a repair case."""
    case_id: str
    event_id: str
    repository_url: str
    repository_name: str = ""
    artifact_dir: str = ""
    status: str = "CREATED"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CaseBundle(BaseModel):
    """The canonical state object for one dependency repair case.

    Populated incrementally as the pipeline stages run. Each section
    corresponds to one stage's output:

      meta              — case identifiers and provenance
      update_evidence   — Stage 1: what was updated and how it was classified
      execution         — Stage 2: before/after Gradle execution results
      structural        — Stage 2/3: KMP-aware structural analysis
      repair            — Stage 3/4: localization candidates and patch attempts
      validation        — Stage 5: multi-target validation results
      explanation       — Stage 5: explanation artifact contents
    """

    meta: CaseMeta
    update_evidence: Optional[UpdateEvidence] = None
    execution: Optional[ExecutionEvidence] = None
    structural: Optional[StructuralEvidence] = None
    repair: Optional[RepairEvidence] = None
    validation: Optional[ValidationEvidence] = None
    explanation: Optional[ExplanationEvidence] = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def case_id(self) -> str:
        return self.meta.case_id

    @property
    def is_complete(self) -> bool:
        """True when all required sections are populated."""
        return all([
            self.update_evidence is not None,
            self.execution is not None,
            self.structural is not None,
            self.repair is not None,
            self.validation is not None,
            self.explanation is not None,
        ])

    def has_execution_errors(self) -> bool:
        if self.execution is None or self.execution.after is None:
            return False
        return bool(self.execution.after.error_observations)

    def localized_files(self, top_k: int = 5) -> list[str]:
        if self.repair is None or self.repair.localization is None:
            return []
        return self.repair.localization.files_at_rank(top_k)

    def accepted_patch(self) -> Optional[PatchAttempt]:
        if self.repair is None:
            return None
        return self.repair.accepted_patch()

    def summary(self) -> str:
        """One-line human-readable summary for logging."""
        repo = self.meta.repository_name or self.meta.repository_url
        vc = ""
        if self.update_evidence and self.update_evidence.version_changes:
            c = self.update_evidence.version_changes[0]
            vc = f" | {c.dependency_group} {c.before}→{c.after}"
        return f"Case {self.case_id[:8]} [{self.meta.status}] {repo}{vc}"

    # ------------------------------------------------------------------
    # Stage write helpers — called by orchestrator, not by agents
    # ------------------------------------------------------------------

    def set_update_evidence(self, evidence: UpdateEvidence) -> None:
        self.update_evidence = evidence
        self.meta.status = "INGESTED"
        self.meta.updated_at = datetime.now(timezone.utc)

    def set_execution_evidence(self, evidence: ExecutionEvidence) -> None:
        self.execution = evidence
        self.meta.status = "EXECUTED"
        self.meta.updated_at = datetime.now(timezone.utc)

    def set_structural_evidence(self, evidence: StructuralEvidence) -> None:
        self.structural = evidence
        self.meta.status = "ANALYZED"
        self.meta.updated_at = datetime.now(timezone.utc)

    def set_localization_result(self, result: LocalizationResult) -> None:
        if self.repair is None:
            self.repair = RepairEvidence()
        self.repair.localization = result
        self.meta.status = "LOCALIZED"
        self.meta.updated_at = datetime.now(timezone.utc)

    def add_patch_attempt(self, attempt: PatchAttempt) -> None:
        if self.repair is None:
            self.repair = RepairEvidence()
        self.repair.patch_attempts.append(attempt)
        self.meta.status = "PATCH_ATTEMPTED"
        self.meta.updated_at = datetime.now(timezone.utc)

    def set_validation_evidence(self, evidence: ValidationEvidence) -> None:
        self.validation = evidence
        self.meta.status = "VALIDATED"
        self.meta.updated_at = datetime.now(timezone.utc)

    def set_explanation_evidence(self, evidence: ExplanationEvidence) -> None:
        self.explanation = evidence
        self.meta.status = "EXPLAINED"
        self.meta.updated_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Agent context builders — restrict context to evidence-backed data
    # ------------------------------------------------------------------

    def localization_context(self) -> dict:
        """Evidence dict for the LocalizationAgent — no raw repo content."""
        return {
            "update": self.update_evidence.model_dump() if self.update_evidence else {},
            "execution_errors": [
                e.model_dump() for e in (
                    self.execution.all_errors("after") if self.execution else []
                )
            ],
            "structural": {
                "direct_import_files": (
                    self.structural.direct_import_files if self.structural else []
                ),
                "expect_actual_pairs": (
                    [p.model_dump() for p in self.structural.expect_actual_pairs]
                    if self.structural else []
                ),
                "relevant_build_files": (
                    self.structural.relevant_build_files if self.structural else []
                ),
            },
        }

    def repair_context(self, top_k: int = 5) -> dict:
        """Evidence dict for the RepairAgent — restricted to localized files."""
        localized = self.localized_files(top_k)
        errors = self.execution.all_errors("after") if self.execution else []
        return {
            "update": self.update_evidence.model_dump() if self.update_evidence else {},
            "localized_files": localized,
            "errors": [e.model_dump() for e in errors],
            "previous_attempts": [
                {"attempt": p.attempt_number, "status": p.status, "reason": p.retry_reason}
                for p in (self.repair.patch_attempts if self.repair else [])
            ],
        }

    def explanation_context(self) -> dict:
        """Full context for the ExplanationAgent."""
        return {
            "update": self.update_evidence.model_dump() if self.update_evidence else {},
            "execution_summary": {
                "before_status": (
                    self.execution.before.overall_status.value
                    if self.execution and self.execution.before else "NOT_RUN"
                ),
                "after_status": (
                    self.execution.after.overall_status.value
                    if self.execution and self.execution.after else "NOT_RUN"
                ),
                "error_count": len(
                    self.execution.all_errors("after") if self.execution else []
                ),
            },
            "localization": (
                self.repair.localization.model_dump()
                if self.repair and self.repair.localization else {}
            ),
            "patch": (
                self.repair.latest_patch().model_dump()
                if self.repair and self.repair.latest_patch() else {}
            ),
            "validation": (
                self.validation.model_dump() if self.validation else {}
            ),
        }
