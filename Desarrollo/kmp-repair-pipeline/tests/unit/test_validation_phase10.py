"""Unit tests for Phase 10 — validator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.case_bundle.evidence import TargetValidation, TaskOutcome
from kmp_repair_pipeline.domain.validation import ValidationStatus
from kmp_repair_pipeline.validation.validator import (
    ValidationResult,
    _aggregate_status,
    _aggregate_status_values,
    validate,
)


# ---------------------------------------------------------------------------
# _aggregate_status_values
# ---------------------------------------------------------------------------


class TestAggregateStatusValues:
    def test_all_success(self) -> None:
        result = _aggregate_status_values([
            ValidationStatus.SUCCESS_REPOSITORY_LEVEL,
            ValidationStatus.SUCCESS_REPOSITORY_LEVEL,
        ])
        assert result == ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value

    def test_any_failed_build(self) -> None:
        result = _aggregate_status_values([
            ValidationStatus.SUCCESS_REPOSITORY_LEVEL,
            ValidationStatus.FAILED_BUILD,
        ])
        assert result == ValidationStatus.FAILED_BUILD.value

    def test_failed_tests_no_build_failure(self) -> None:
        result = _aggregate_status_values([
            ValidationStatus.SUCCESS_REPOSITORY_LEVEL,
            ValidationStatus.FAILED_TESTS,
        ])
        assert result == ValidationStatus.FAILED_TESTS.value

    def test_build_failure_dominates_test_failure(self) -> None:
        result = _aggregate_status_values([
            ValidationStatus.FAILED_TESTS,
            ValidationStatus.FAILED_BUILD,
        ])
        assert result == ValidationStatus.FAILED_BUILD.value

    def test_empty_returns_not_run_yet(self) -> None:
        result = _aggregate_status_values([])
        assert result == ValidationStatus.NOT_RUN_YET.value

    def test_accepts_string_values(self) -> None:
        result = _aggregate_status_values([
            ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value,
            ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value,
        ])
        assert result == ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value

    def test_inconclusive_fallback(self) -> None:
        result = _aggregate_status_values([ValidationStatus.INCONCLUSIVE])
        assert result == ValidationStatus.INCONCLUSIVE.value


# ---------------------------------------------------------------------------
# _aggregate_status (TaskOutcome list)
# ---------------------------------------------------------------------------


class TestAggregateStatus:
    def _outcome(self, status: ValidationStatus) -> TaskOutcome:
        return TaskOutcome(task_name="t", status=status)

    def test_success(self) -> None:
        outcomes = [self._outcome(ValidationStatus.SUCCESS_REPOSITORY_LEVEL)]
        assert _aggregate_status(outcomes) == ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value

    def test_empty(self) -> None:
        assert _aggregate_status([]) == ValidationStatus.NOT_RUN_YET.value

    def test_failed_build(self) -> None:
        outcomes = [
            self._outcome(ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
            self._outcome(ValidationStatus.FAILED_BUILD),
        ]
        assert _aggregate_status(outcomes) == ValidationStatus.FAILED_BUILD.value


# ---------------------------------------------------------------------------
# validate() — orchestrator (patched DB + Gradle)
# ---------------------------------------------------------------------------


def _make_bundle():
    from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
    from kmp_repair_pipeline.case_bundle.evidence import (
        ExecutionEvidence, LocalizationResult, RepairEvidence,
        RevisionExecution, SourceSetMap, StructuralEvidence, UpdateEvidence,
    )
    from kmp_repair_pipeline.domain.events import (
        DependencyUpdateEvent, UpdateClass, VersionChange,
    )
    from kmp_repair_pipeline.domain.validation import ValidationStatus

    bundle = CaseBundle(
        meta=CaseMeta(
            case_id="case-010",
            event_id="ev-10",
            repository_url="https://github.com/test/repo",
            status="PATCH_ATTEMPTED",
        )
    )
    bundle.update_evidence = UpdateEvidence(
        update_event=DependencyUpdateEvent(repo_url="https://github.com/test/repo"),
        version_changes=[
            VersionChange(dependency_group="io.ktor", version_key="ktor",
                          before="3.1.3", after="3.4.1")
        ],
        update_class=UpdateClass.DIRECT_LIBRARY,
    )
    bundle.execution = ExecutionEvidence(
        after=RevisionExecution(
            revision_type="after",
            overall_status=ValidationStatus.FAILED_BUILD,
            error_observations=[],
        )
    )
    bundle.repair = RepairEvidence(
        localization=LocalizationResult(candidates=[])
    )
    bundle.structural = StructuralEvidence(
        source_set_map=SourceSetMap(),
        total_kotlin_files=3,
    )
    return bundle


class TestValidate:
    def _make_attempt_row(self, status: str = "APPLIED", attempt_number: int = 1) -> MagicMock:
        row = MagicMock()
        row.id = "attempt-id-001"
        row.repair_case_id = "case-010"
        row.attempt_number = attempt_number
        row.repair_mode = "full_thesis"
        row.status = status
        return row

    def _make_after_rev(self, tmp_path: Path) -> MagicMock:
        rev = MagicMock()
        rev.local_path = str(tmp_path / "after")
        (tmp_path / "after").mkdir(parents=True, exist_ok=True)
        return rev

    def _mock_gradle_success(self) -> MagicMock:
        from kmp_repair_pipeline.runners.gradle_runner import GradleRunResult
        result = MagicMock(spec=GradleRunResult)
        result.task_name = "compileCommonMainKotlinMetadata"
        result.exit_code = 0
        result.status = ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
        result.duration_s = 1.5
        result.stdout = "BUILD SUCCESSFUL"
        result.stderr = ""
        result.error_observations = []
        return result

    def test_validate_applied_patch_sets_validated(self, tmp_path: Path) -> None:
        bundle = _make_bundle()
        session = MagicMock()
        attempt_row = self._make_attempt_row()
        after_rev = self._make_after_rev(tmp_path)

        exec_run = MagicMock()
        exec_run.id = "exec-run-001"

        store = MagicMock()
        store.write_task_output.return_value = ("/p/out.txt", "sha1", "/p/err.txt", "sha2")

        with (
            patch("kmp_repair_pipeline.validation.validator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.validation.validator.to_db"),
            patch("kmp_repair_pipeline.validation.validator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.validation.validator.RevisionRepo") as MockRev,
            patch("kmp_repair_pipeline.validation.validator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.validation.validator.ExecutionRunRepo") as MockExecRun,
            patch("kmp_repair_pipeline.validation.validator.TaskResultRepo") as MockTask,
            patch("kmp_repair_pipeline.validation.validator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.validation.validator.RepairCaseRepo") as MockCase,
            patch("kmp_repair_pipeline.validation.validator.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.validation.validator.detect") as mock_detect,
            patch("kmp_repair_pipeline.validation.validator.run_tasks") as mock_run_tasks,
        ):
            MockPatch.return_value.list_for_case.return_value = [attempt_row]
            MockRev.return_value.get.return_value = after_rev
            MockExecRun.return_value.create.return_value = exec_run
            MockTask.return_value.create.return_value = MagicMock()
            MockValRun.return_value.create.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()
            MockStore.return_value = store

            env = MagicMock()
            env.runnable_targets = ["shared"]
            env.unavailable_targets = {}
            mock_detect.return_value = env

            mock_run_tasks.return_value = [self._mock_gradle_success()]

            result = validate(
                case_id="case-010",
                session=session,
                artifact_base=tmp_path / "artifacts",
            )

        assert result.patch_status == "VALIDATED"
        assert result.overall_status == ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
        assert result.patch_attempt_number == 1
        assert len(result.target_results) == 1
        assert result.target_results[0].target == "shared"

    def test_validate_failed_build_sets_rejected(self, tmp_path: Path) -> None:
        bundle = _make_bundle()
        session = MagicMock()
        attempt_row = self._make_attempt_row()
        after_rev = self._make_after_rev(tmp_path)

        exec_run = MagicMock()
        exec_run.id = "exec-run-002"

        store = MagicMock()
        store.write_task_output.return_value = ("/p/out.txt", "sha1", "/p/err.txt", "sha2")

        def _failing_gradle(*args, **kwargs):
            from kmp_repair_pipeline.runners.gradle_runner import GradleRunResult
            r = MagicMock(spec=GradleRunResult)
            r.task_name = "compileCommonMainKotlinMetadata"
            r.exit_code = 1
            r.status = ValidationStatus.FAILED_BUILD.value
            r.duration_s = 0.5
            r.stdout = ""
            r.stderr = "error: unresolved reference"
            r.error_observations = []
            return [r]

        with (
            patch("kmp_repair_pipeline.validation.validator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.validation.validator.to_db"),
            patch("kmp_repair_pipeline.validation.validator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.validation.validator.RevisionRepo") as MockRev,
            patch("kmp_repair_pipeline.validation.validator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.validation.validator.ExecutionRunRepo") as MockExecRun,
            patch("kmp_repair_pipeline.validation.validator.TaskResultRepo") as MockTask,
            patch("kmp_repair_pipeline.validation.validator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.validation.validator.RepairCaseRepo") as MockCase,
            patch("kmp_repair_pipeline.validation.validator.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.validation.validator.detect") as mock_detect,
            patch("kmp_repair_pipeline.validation.validator.run_tasks", side_effect=_failing_gradle),
        ):
            MockPatch.return_value.list_for_case.return_value = [attempt_row]
            MockRev.return_value.get.return_value = after_rev
            MockExecRun.return_value.create.return_value = exec_run
            MockTask.return_value.create.return_value = MagicMock()
            MockValRun.return_value.create.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()
            MockStore.return_value = store

            env = MagicMock()
            env.runnable_targets = ["shared"]
            env.unavailable_targets = {}
            mock_detect.return_value = env

            result = validate(
                case_id="case-010",
                session=session,
                artifact_base=tmp_path / "artifacts",
            )

        assert result.patch_status == "REJECTED"
        assert result.overall_status == ValidationStatus.FAILED_BUILD.value

    def test_no_applied_attempt_raises(self, tmp_path: Path) -> None:
        bundle = _make_bundle()
        session = MagicMock()

        with (
            patch("kmp_repair_pipeline.validation.validator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.validation.validator.PatchAttemptRepo") as MockPatch,
        ):
            MockPatch.return_value.list_for_case.return_value = []

            with pytest.raises(ValueError, match="no APPLIED patch attempt"):
                validate(case_id="case-010", session=session)

    def test_unavailable_target_recorded(self, tmp_path: Path) -> None:
        bundle = _make_bundle()
        session = MagicMock()
        attempt_row = self._make_attempt_row()
        after_rev = self._make_after_rev(tmp_path)

        exec_run = MagicMock()
        exec_run.id = "exec-run-003"

        store = MagicMock()
        store.write_task_output.return_value = ("/p/out.txt", "sha1", "/p/err.txt", "sha2")

        with (
            patch("kmp_repair_pipeline.validation.validator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.validation.validator.to_db"),
            patch("kmp_repair_pipeline.validation.validator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.validation.validator.RevisionRepo") as MockRev,
            patch("kmp_repair_pipeline.validation.validator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.validation.validator.ExecutionRunRepo") as MockExecRun,
            patch("kmp_repair_pipeline.validation.validator.TaskResultRepo") as MockTask,
            patch("kmp_repair_pipeline.validation.validator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.validation.validator.RepairCaseRepo") as MockCase,
            patch("kmp_repair_pipeline.validation.validator.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.validation.validator.detect") as mock_detect,
            patch("kmp_repair_pipeline.validation.validator.run_tasks") as mock_run_tasks,
        ):
            MockPatch.return_value.list_for_case.return_value = [attempt_row]
            MockRev.return_value.get.return_value = after_rev
            MockExecRun.return_value.create.return_value = exec_run
            MockTask.return_value.create.return_value = MagicMock()
            MockValRun.return_value.create.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()
            MockStore.return_value = store

            env = MagicMock()
            env.runnable_targets = ["shared"]
            env.unavailable_targets = {"ios": "Xcode not found"}
            mock_detect.return_value = env

            mock_run_tasks.return_value = [self._mock_gradle_success()]

            result = validate(
                case_id="case-010",
                session=session,
                artifact_base=tmp_path / "artifacts",
            )

        # ios should appear as NOT_RUN_ENVIRONMENT_UNAVAILABLE
        ios_result = next((r for r in result.target_results if r.target == "ios"), None)
        assert ios_result is not None
        assert ios_result.status == ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE
        # shared succeeded, so overall is VALIDATED (only runnable targets counted)
        assert result.patch_status == "VALIDATED"

    def test_specific_attempt_id_resolved(self, tmp_path: Path) -> None:
        bundle = _make_bundle()
        session = MagicMock()
        attempt_row = self._make_attempt_row()
        after_rev = self._make_after_rev(tmp_path)

        exec_run = MagicMock()
        exec_run.id = "exec-run-004"

        store = MagicMock()
        store.write_task_output.return_value = ("/p/out.txt", "sha1", "/p/err.txt", "sha2")

        with (
            patch("kmp_repair_pipeline.validation.validator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.validation.validator.to_db"),
            patch("kmp_repair_pipeline.validation.validator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.validation.validator.RevisionRepo") as MockRev,
            patch("kmp_repair_pipeline.validation.validator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.validation.validator.ExecutionRunRepo") as MockExecRun,
            patch("kmp_repair_pipeline.validation.validator.TaskResultRepo") as MockTask,
            patch("kmp_repair_pipeline.validation.validator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.validation.validator.RepairCaseRepo") as MockCase,
            patch("kmp_repair_pipeline.validation.validator.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.validation.validator.detect") as mock_detect,
            patch("kmp_repair_pipeline.validation.validator.run_tasks") as mock_run_tasks,
        ):
            MockPatch.return_value.get_by_id.return_value = attempt_row
            MockRev.return_value.get.return_value = after_rev
            MockExecRun.return_value.create.return_value = exec_run
            MockTask.return_value.create.return_value = MagicMock()
            MockValRun.return_value.create.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()
            MockStore.return_value = store

            env = MagicMock()
            env.runnable_targets = ["shared"]
            env.unavailable_targets = {}
            mock_detect.return_value = env

            mock_run_tasks.return_value = [self._mock_gradle_success()]

            result = validate(
                case_id="case-010",
                session=session,
                artifact_base=tmp_path / "artifacts",
                patch_attempt_id="attempt-id-001",
            )

        # Should have used get_by_id, not list_for_case
        MockPatch.return_value.get_by_id.assert_called_once_with("attempt-id-001")
        assert result.patch_attempt_id == "attempt-id-001"
