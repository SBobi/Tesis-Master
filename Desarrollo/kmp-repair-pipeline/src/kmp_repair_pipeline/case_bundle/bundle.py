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
                "version_catalog": (
                    self.structural.version_catalog if self.structural else {}
                ),
            },
        }

    def repair_context(self, top_k: int = 5) -> dict:
        """Evidence dict for the RepairAgent — restricted to localized files.

        When a top-k localized file participates in an expect/actual pair, its
        counterparts (the ``actual_files`` or the ``expect_file``) are appended
        to ``localized_files`` even if they fall outside the top-k ranking.
        This ensures the RepairAgent always has both sides of a KMP contract in
        context and does not generate half-patched expect/actual mismatches.
        """
        localized = self.localized_files(top_k)

        # ── Expect/actual coupling ──────────────────────────────────────────
        # Build a lookup: each file → its coupled counterparts.
        if self.structural and self.structural.expect_actual_pairs:
            coupled: set[str] = set()
            localized_set = set(localized)
            for pair in self.structural.expect_actual_pairs:
                if pair.expect_file in localized_set:
                    # A localized expect file — include all its actual counterparts
                    coupled.update(pair.actual_files)
                elif any(f in localized_set for f in pair.actual_files):
                    # A localized actual file — include the expect declaration
                    coupled.add(pair.expect_file)
            # Append newly-discovered coupled files (preserve order, no duplicates)
            for f in coupled:
                if f not in localized_set:
                    localized = localized + [f]
        errors = self.execution.all_errors("after") if self.execution else []

        # Collect ALL required Kotlin versions from KLIB_ABI_ERROR and JVM
        # metadata errors, then take the MAXIMUM.  Multiple libraries may
        # demand different minimum Kotlin versions:
        #   koin 4.1.0  → produced by Kotlin 2.1.20  (min required = 2.1.20)
        #   ktor 3.4.1  → JVM metadata says binary=2.3.0  (min required = 2.3.0)
        # The correct `kotlin` alias target is max(2.1.20, 2.3.0) = "2.3.0".
        # Taking only the FIRST is wrong when multiple libraries conflict.
        required_kotlin_version: Optional[str] = _max_kotlin_version(
            [getattr(e, "required_kotlin_version", None) for e in errors]
        )

        # Build a cascade conflict map: library → version it requires.
        # This lets the agent see ALL constraints at once, not just the max.
        # Example: {"koin-core-iosArm64Main": "2.1.20", "ktor-client-core-jvm": "2.3.0"}
        kotlin_cascade: dict[str, str] = {}
        for e in errors:
            ver = getattr(e, "required_kotlin_version", None)
            if ver and e.message:
                # Extract a short library name from the message for display
                import re as _re
                m = _re.search(r"'([\w.-]+-jvm|[\w.-]+iosArm64Main|[\w.-]+Main)[-/]", e.message)
                if m:
                    kotlin_cascade[m.group(1)] = ver
                elif ver not in kotlin_cascade.values():
                    kotlin_cascade[f"lib_{len(kotlin_cascade)}"] = ver

        # ── Catalog diff (alias renames + artifact renames) ────────────────
        catalog_alias_diff: dict = {}
        artifact_renames: list[dict] = []
        if self.update_evidence:
            catalog_alias_diff = self.update_evidence.catalog_alias_diff or {}
            artifact_renames = self.update_evidence.artifact_renames or []

        return {
            "update": self.update_evidence.model_dump() if self.update_evidence else {},
            "localized_files": localized,
            "errors": [e.model_dump() for e in errors],
            "previous_attempts": [
                _enrich_attempt_entry(p)
                for p in (self.repair.patch_attempts if self.repair else [])
            ],
            # Parsed version catalog from libs.versions.toml — key tool for
            # detecting Kotlin ABI incompatibilities and other version-bump fixes.
            "version_catalog": (
                self.structural.version_catalog if self.structural else {}
            ),
            # MAX required Kotlin version across all KLIB + JVM metadata errors.
            # This is the version to bump `kotlin` TO — the minimum that satisfies
            # all library constraints simultaneously.
            "required_kotlin_version": required_kotlin_version,
            # Full cascade map: library → required Kotlin version (for transparency).
            # Lets the agent understand the multi-library constraint landscape.
            "kotlin_cascade_constraints": kotlin_cascade,
            # Catalog structural changes: alias renames and artifact module changes.
            # These are invisible to the agent without a before/after diff since
            # they surface as "Unresolved reference" errors with no direct fix hint.
            "catalog_alias_diff": catalog_alias_diff,
            "artifact_renames": artifact_renames,
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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _enrich_attempt_entry(p: "PatchAttempt") -> dict:
    """Build the ``previous_attempts`` entry for one PatchAttempt.

    For REJECTED attempts that carry a JSON ``retry_reason`` produced by the
    validate-in-loop, the ``remaining_errors`` list is surfaced so the next
    RepairAgent call can see exactly which errors survived.  For all other
    attempts the entry is the same as before.
    """
    import json as _json

    entry: dict = {
        "attempt": p.attempt_number,
        "status": p.status,
        "reason": p.retry_reason,
    }
    if p.status == "REJECTED" and p.retry_reason:
        try:
            parsed = _json.loads(p.retry_reason)
            remaining = parsed.get("remaining_errors")
            if remaining is not None:
                entry["remaining_errors"] = remaining
        except (ValueError, TypeError):
            pass  # plain-text retry_reason — leave entry as-is
    return entry


def _max_kotlin_version(versions: list[Optional[str]]) -> Optional[str]:
    """Return the semantically highest Kotlin version from a list, ignoring None.

    Kotlin versions follow PEP-440/semver conventions (e.g. "2.3.0", "2.1.20").
    We compare as tuples of ints so "2.1.20" > "2.1.9" and "2.3.0" > "2.1.20".

    When multiple libraries impose different minimum Kotlin requirements, the
    project must satisfy the HIGHEST constraint to be compatible with all of
    them.  Example:
        koin 4.1.0  → required_kotlin_version = "2.1.20"
        ktor 3.4.1  → required_kotlin_version = "2.3.0"
        max("2.1.20", "2.3.0") = "2.3.0"  ← the version to bump `kotlin` to.
    """
    candidates: list[str] = [v for v in versions if v]
    if not candidates:
        return None

    def _semver(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0,)

    return max(candidates, key=_semver)
