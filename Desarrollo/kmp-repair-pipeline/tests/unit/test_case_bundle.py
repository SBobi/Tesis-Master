"""Unit tests for the typed Case Bundle."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from kmp_repair_pipeline.case_bundle import (
    CaseBundle,
    CaseMeta,
    ErrorObservation,
    ExecutionEvidence,
    ExplanationEvidence,
    LocalizationResult,
    PatchAttempt,
    RepairEvidence,
    RevisionExecution,
    SourceSetMap,
    StructuralEvidence,
    TargetValidation,
    TaskOutcome,
    UpdateEvidence,
    ValidationEvidence,
    load_snapshot,
    save_snapshot,
)
from kmp_repair_pipeline.domain.analysis import ExpectActualPair, ImpactGraph
from kmp_repair_pipeline.domain.events import DependencyUpdateEvent, UpdateClass, VersionChange
from kmp_repair_pipeline.domain.validation import ValidationStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_meta(case_id: str = "case-001") -> CaseMeta:
    return CaseMeta(
        case_id=case_id,
        event_id="event-001",
        repository_url="https://github.com/example/kmp-app",
        repository_name="kmp-app",
        status="CREATED",
    )


def make_update_evidence() -> UpdateEvidence:
    vc = VersionChange(dependency_group="io.ktor", version_key="ktor", before="3.1.3", after="3.4.1")
    return UpdateEvidence(
        update_event=DependencyUpdateEvent(repo_url="https://github.com/example/kmp-app"),
        version_changes=[vc],
        update_class=UpdateClass.DIRECT_LIBRARY,
    )


def make_execution_evidence(has_errors: bool = True) -> ExecutionEvidence:
    error = ErrorObservation(
        error_type="COMPILE_ERROR",
        file_path="src/commonMain/kotlin/App.kt",
        line=42,
        message="Unresolved reference: HttpClient",
    )
    task = TaskOutcome(
        task_name=":compileCommonMainKotlinMetadata",
        exit_code=1,
        status=ValidationStatus.FAILED_BUILD,
    )
    after_rev = RevisionExecution(
        revision_type="after",
        overall_status=ValidationStatus.FAILED_BUILD,
        task_outcomes=[task],
        error_observations=[error] if has_errors else [],
    )
    return ExecutionEvidence(after=after_rev)


def make_localization_result() -> LocalizationResult:
    return LocalizationResult(
        candidates=[
            LocalizationResult.Candidate(
                rank=1,
                file_path="src/commonMain/kotlin/App.kt",
                source_set="common",
                classification="shared_code",
                score=0.95,
                score_breakdown={"static": 0.8, "dynamic": 0.15},
            ),
            LocalizationResult.Candidate(
                rank=2,
                file_path="src/androidMain/kotlin/AppAndroid.kt",
                source_set="android",
                classification="platform_specific",
                score=0.60,
            ),
        ]
    )


def make_bundle(case_id: str = "case-001") -> CaseBundle:
    return CaseBundle(meta=make_meta(case_id))


# ---------------------------------------------------------------------------
# CaseMeta
# ---------------------------------------------------------------------------


class TestCaseMeta:
    def test_defaults(self) -> None:
        meta = CaseMeta(case_id="x", event_id="e", repository_url="https://example.com")
        assert meta.status == "CREATED"
        assert meta.artifact_dir == ""

    def test_json_round_trip(self) -> None:
        meta = make_meta()
        restored = CaseMeta.model_validate_json(meta.model_dump_json())
        assert restored == meta


# ---------------------------------------------------------------------------
# UpdateEvidence
# ---------------------------------------------------------------------------


class TestUpdateEvidence:
    def test_version_changes_populated(self) -> None:
        ev = make_update_evidence()
        assert len(ev.version_changes) == 1
        assert ev.version_changes[0].dependency_group == "io.ktor"
        assert ev.update_class == UpdateClass.DIRECT_LIBRARY

    def test_auxiliary_fields_optional(self) -> None:
        ev = make_update_evidence()
        assert ev.github_dep_graph_path is None
        assert ev.sbom_path is None


# ---------------------------------------------------------------------------
# ExecutionEvidence
# ---------------------------------------------------------------------------


class TestExecutionEvidence:
    def test_failing_tasks(self) -> None:
        ev = make_execution_evidence()
        failing = ev.failing_tasks("after")
        assert len(failing) == 1
        assert failing[0].task_name == ":compileCommonMainKotlinMetadata"

    def test_all_errors(self) -> None:
        ev = make_execution_evidence()
        errors = ev.all_errors("after")
        assert len(errors) == 1
        assert errors[0].line == 42

    def test_no_errors_when_clean(self) -> None:
        ev = make_execution_evidence(has_errors=False)
        assert ev.all_errors("after") == []

    def test_before_not_run(self) -> None:
        ev = make_execution_evidence()
        assert ev.failing_tasks("before") == []


# ---------------------------------------------------------------------------
# StructuralEvidence
# ---------------------------------------------------------------------------


class TestStructuralEvidence:
    def test_source_set_map_lookup(self) -> None:
        ssm = SourceSetMap(
            common_files=["src/commonMain/kotlin/App.kt"],
            android_files=["src/androidMain/kotlin/AppAndroid.kt"],
        )
        assert ssm.source_set_for("src/commonMain/kotlin/App.kt") == "common"
        assert ssm.source_set_for("src/androidMain/kotlin/AppAndroid.kt") == "android"
        assert ssm.source_set_for("unknown.kt") == "unknown"


# ---------------------------------------------------------------------------
# RepairEvidence
# ---------------------------------------------------------------------------


class TestRepairEvidence:
    def test_localization_top_k(self) -> None:
        loc = make_localization_result()
        top1 = loc.top_k(1)
        assert len(top1) == 1
        assert top1[0].file_path == "src/commonMain/kotlin/App.kt"

    def test_files_at_rank(self) -> None:
        loc = make_localization_result()
        files = loc.files_at_rank(2)
        assert "src/commonMain/kotlin/App.kt" in files
        assert "src/androidMain/kotlin/AppAndroid.kt" in files

    def test_latest_and_accepted_patch(self) -> None:
        re = RepairEvidence()
        re.patch_attempts = [
            PatchAttempt(attempt_number=1, repair_mode="full_thesis", status="REJECTED"),
            PatchAttempt(attempt_number=2, repair_mode="full_thesis", status="VALIDATED"),
        ]
        assert re.latest_patch().attempt_number == 2
        assert re.accepted_patch().attempt_number == 2

    def test_no_accepted_patch_when_all_rejected(self) -> None:
        re = RepairEvidence()
        re.patch_attempts = [
            PatchAttempt(attempt_number=1, repair_mode="full_thesis", status="REJECTED"),
        ]
        assert re.accepted_patch() is None


# ---------------------------------------------------------------------------
# ValidationEvidence
# ---------------------------------------------------------------------------


class TestValidationEvidence:
    def test_unavailable_target(self) -> None:
        ve = ValidationEvidence(target_results=[
            TargetValidation(
                target="ios",
                status=ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE,
                unavailable_reason="Xcode not available",
            )
        ])
        assert ve.has_unavailable_target() is True

    def test_all_required_passed(self) -> None:
        ve = ValidationEvidence(target_results=[
            TargetValidation(target="shared", status=ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
            TargetValidation(target="android", status=ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
        ])
        assert ve.all_required_passed(["shared", "android"]) is True
        assert ve.all_required_passed(["shared", "android", "ios"]) is False

    def test_result_for_target(self) -> None:
        ve = ValidationEvidence(target_results=[
            TargetValidation(target="android", status=ValidationStatus.FAILED_BUILD),
        ])
        r = ve.result_for("android")
        assert r is not None
        assert r.status == ValidationStatus.FAILED_BUILD
        assert ve.result_for("ios") is None


# ---------------------------------------------------------------------------
# CaseBundle
# ---------------------------------------------------------------------------


class TestCaseBundle:
    def test_initial_state(self) -> None:
        bundle = make_bundle()
        assert bundle.meta.status == "CREATED"
        assert bundle.is_complete is False
        assert bundle.has_execution_errors() is False
        assert bundle.localized_files() == []

    def test_stage_writes_update_status(self) -> None:
        bundle = make_bundle()
        bundle.set_update_evidence(make_update_evidence())
        assert bundle.meta.status == "INGESTED"

        bundle.set_execution_evidence(make_execution_evidence())
        assert bundle.meta.status == "EXECUTED"
        assert bundle.has_execution_errors() is True

    def test_localized_files(self) -> None:
        bundle = make_bundle()
        bundle.set_localization_result(make_localization_result())
        assert bundle.meta.status == "LOCALIZED"
        assert "src/commonMain/kotlin/App.kt" in bundle.localized_files(top_k=5)

    def test_add_patch_attempt(self) -> None:
        bundle = make_bundle()
        bundle.add_patch_attempt(PatchAttempt(attempt_number=1, repair_mode="full_thesis"))
        assert bundle.meta.status == "PATCH_ATTEMPTED"
        assert bundle.accepted_patch() is None

        bundle.repair.patch_attempts[0].status = "VALIDATED"
        assert bundle.accepted_patch() is not None

    def test_summary_string(self) -> None:
        bundle = make_bundle()
        bundle.set_update_evidence(make_update_evidence())
        s = bundle.summary()
        assert "io.ktor" in s
        assert "INGESTED" in s

    def test_localization_context_shape(self) -> None:
        bundle = make_bundle()
        bundle.set_update_evidence(make_update_evidence())
        bundle.set_execution_evidence(make_execution_evidence())
        ctx = bundle.localization_context()
        assert "update" in ctx
        assert "execution_errors" in ctx
        assert "structural" in ctx
        assert len(ctx["execution_errors"]) == 1

    def test_repair_context_shape(self) -> None:
        bundle = make_bundle()
        bundle.set_update_evidence(make_update_evidence())
        bundle.set_execution_evidence(make_execution_evidence())
        bundle.set_localization_result(make_localization_result())
        ctx = bundle.repair_context(top_k=3)
        assert "localized_files" in ctx
        assert len(ctx["localized_files"]) >= 1


# ---------------------------------------------------------------------------
# JSON snapshot round-trip
# ---------------------------------------------------------------------------


class TestSnapshotSerialization:
    def test_save_and_load_snapshot(self, tmp_path: Path) -> None:
        bundle = make_bundle("snap-case")
        bundle.set_update_evidence(make_update_evidence())
        bundle.set_execution_evidence(make_execution_evidence())
        bundle.set_localization_result(make_localization_result())

        path = tmp_path / "bundle.json"
        save_snapshot(bundle, path)
        assert path.exists()

        restored = load_snapshot(path)
        assert restored.case_id == "snap-case"
        assert restored.meta.status == "LOCALIZED"
        assert len(restored.update_evidence.version_changes) == 1
        assert restored.execution.after.overall_status == ValidationStatus.FAILED_BUILD

    def test_snapshot_is_valid_json(self, tmp_path: Path) -> None:
        bundle = make_bundle("json-case")
        bundle.set_update_evidence(make_update_evidence())
        path = tmp_path / "bundle.json"
        save_snapshot(bundle, path)
        data = json.loads(path.read_text())
        assert data["meta"]["case_id"] == "json-case"
        assert data["update_evidence"]["update_class"] == "direct_library"

    def test_empty_bundle_round_trip(self, tmp_path: Path) -> None:
        bundle = make_bundle("empty-case")
        path = tmp_path / "empty.json"
        save_snapshot(bundle, path)
        restored = load_snapshot(path)
        assert restored.update_evidence is None
        assert restored.execution is None
        assert restored.is_complete is False
