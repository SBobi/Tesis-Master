"""Unit tests for Phase 12 — metrics and evaluator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.case_bundle.evidence import (
    ErrorObservation,
    TargetValidation,
    ValidationEvidence,
)
from kmp_repair_pipeline.domain.validation import ValidationStatus
from kmp_repair_pipeline.evaluation.metrics import (
    CaseMetrics,
    compute_bsr,
    compute_ctsr,
    compute_efr,
    compute_ffsr,
    compute_hit_at_k,
    compute_metrics,
    compute_source_set_accuracy,
)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _validation(targets: list[tuple[str, ValidationStatus]]) -> ValidationEvidence:
    results = [TargetValidation(target=t, status=s) for t, s in targets]
    ev = ValidationEvidence(target_results=results)
    # Derive repository_level_status
    statuses = [r.status for r in results]
    if all(s == ValidationStatus.SUCCESS_REPOSITORY_LEVEL for s in statuses):
        ev.repository_level_status = ValidationStatus.SUCCESS_REPOSITORY_LEVEL
    elif any(s == ValidationStatus.FAILED_BUILD for s in statuses):
        ev.repository_level_status = ValidationStatus.FAILED_BUILD
    elif any(s == ValidationStatus.FAILED_TESTS for s in statuses):
        ev.repository_level_status = ValidationStatus.FAILED_TESTS
    else:
        ev.repository_level_status = ValidationStatus.INCONCLUSIVE
    return ev


def _err(error_type: str = "COMPILE_ERROR", file_path: str = "App.kt",
         line: int = 1, message: str = "unresolved") -> ErrorObservation:
    return ErrorObservation(error_type=error_type, file_path=file_path,
                            line=line, message=message)


# ---------------------------------------------------------------------------
# compute_bsr
# ---------------------------------------------------------------------------


class TestComputeBsr:
    def test_success(self) -> None:
        v = _validation([("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL)])
        assert compute_bsr(v) == 1.0

    def test_failed_build(self) -> None:
        v = _validation([("shared", ValidationStatus.FAILED_BUILD)])
        assert compute_bsr(v) == 0.0

    def test_none_validation(self) -> None:
        assert compute_bsr(None) == 0.0

    def test_failed_tests_is_not_bsr(self) -> None:
        v = _validation([("shared", ValidationStatus.FAILED_TESTS)])
        assert compute_bsr(v) == 0.0


# ---------------------------------------------------------------------------
# compute_ctsr
# ---------------------------------------------------------------------------


class TestComputeCtsr:
    def test_success(self) -> None:
        v = _validation([("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL)])
        assert compute_ctsr(v) == 1.0

    def test_failed_build_returns_0(self) -> None:
        v = _validation([("shared", ValidationStatus.FAILED_BUILD)])
        assert compute_ctsr(v) == 0.0

    def test_failed_tests_still_ctsr(self) -> None:
        # Tests fail but compile succeeded → CTSR = 1.0
        v = _validation([("shared", ValidationStatus.FAILED_TESTS)])
        assert compute_ctsr(v) == 1.0

    def test_unavailable_ignored(self) -> None:
        v = _validation([
            ("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
            ("ios", ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE),
        ])
        assert compute_ctsr(v) == 1.0

    def test_none_returns_0(self) -> None:
        assert compute_ctsr(None) == 0.0


# ---------------------------------------------------------------------------
# compute_ffsr
# ---------------------------------------------------------------------------


class TestComputeFfsr:
    def test_all_success(self) -> None:
        v = _validation([
            ("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
            ("android", ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
        ])
        assert compute_ffsr(v) == 1.0

    def test_one_failed(self) -> None:
        v = _validation([
            ("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL),
            ("android", ValidationStatus.FAILED_TESTS),
        ])
        assert compute_ffsr(v) == 0.0

    def test_all_unavailable_returns_0(self) -> None:
        v = _validation([("ios", ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE)])
        assert compute_ffsr(v) == 0.0

    def test_none_returns_0(self) -> None:
        assert compute_ffsr(None) == 0.0


# ---------------------------------------------------------------------------
# compute_efr
# ---------------------------------------------------------------------------


class TestComputeEfr:
    def test_all_fixed(self) -> None:
        original = [_err(line=10), _err(line=20)]
        assert compute_efr(original, []) == 1.0

    def test_none_fixed(self) -> None:
        original = [_err(line=10)]
        remaining = [_err(line=10)]
        assert compute_efr(original, remaining) == 0.0

    def test_half_fixed(self) -> None:
        original = [_err(line=10), _err(line=20)]
        remaining = [_err(line=10)]
        assert compute_efr(original, remaining) == 0.5

    def test_empty_original_returns_none(self) -> None:
        assert compute_efr([], []) is None

    def test_new_errors_in_remaining_not_counted(self) -> None:
        # New error introduced by patch — doesn't affect EFR of original
        original = [_err(line=10)]
        remaining = [_err(line=10), _err(line=99, message="new error")]
        assert compute_efr(original, remaining) == 0.0


# ---------------------------------------------------------------------------
# compute_hit_at_k
# ---------------------------------------------------------------------------


class TestComputeHitAtK:
    def test_hit_at_1_found(self) -> None:
        candidates = ["src/commonMain/App.kt", "src/commonMain/Util.kt"]
        gt = ["src/commonMain/App.kt"]
        assert compute_hit_at_k(candidates, gt, k=1) == 1.0

    def test_hit_at_1_miss(self) -> None:
        candidates = ["src/commonMain/Util.kt"]
        gt = ["src/commonMain/App.kt"]
        assert compute_hit_at_k(candidates, gt, k=1) == 0.0

    def test_hit_at_3_second_candidate(self) -> None:
        candidates = ["a.kt", "b.kt", "c.kt"]
        gt = ["b.kt"]
        assert compute_hit_at_k(candidates, gt, k=3) == 1.0
        assert compute_hit_at_k(candidates, gt, k=1) == 0.0

    def test_empty_gt_returns_none(self) -> None:
        assert compute_hit_at_k(["a.kt"], [], k=1) is None

    def test_empty_candidates_is_miss(self) -> None:
        assert compute_hit_at_k([], ["a.kt"], k=1) == 0.0


# ---------------------------------------------------------------------------
# compute_source_set_accuracy
# ---------------------------------------------------------------------------


class TestComputeSourceSetAccuracy:
    def test_all_correct(self) -> None:
        candidates = [
            {"file_path": "App.kt", "source_set": "common"},
            {"file_path": "Platform.kt", "source_set": "android"},
        ]
        gt = {"App.kt": "common", "Platform.kt": "android"}
        assert compute_source_set_accuracy(candidates, gt) == 1.0

    def test_half_correct(self) -> None:
        candidates = [
            {"file_path": "App.kt", "source_set": "common"},
            {"file_path": "Platform.kt", "source_set": "wrong"},
        ]
        gt = {"App.kt": "common", "Platform.kt": "android"}
        assert compute_source_set_accuracy(candidates, gt) == 0.5

    def test_empty_gt_returns_none(self) -> None:
        candidates = [{"file_path": "App.kt", "source_set": "common"}]
        assert compute_source_set_accuracy(candidates, {}) is None

    def test_candidates_not_in_gt_ignored(self) -> None:
        # Only files in gt are scored
        candidates = [
            {"file_path": "App.kt", "source_set": "common"},
            {"file_path": "Unknown.kt", "source_set": "wrong"},
        ]
        gt = {"App.kt": "common"}
        assert compute_source_set_accuracy(candidates, gt) == 1.0


# ---------------------------------------------------------------------------
# compute_metrics (integration of all sub-metrics)
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_full_success_case(self) -> None:
        v = _validation([("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL)])
        candidates = [{"file_path": "App.kt", "source_set": "common"}]
        original = [_err(line=5)]

        m = compute_metrics(
            case_id="c1",
            repair_mode="full_thesis",
            validation=v,
            original_errors=original,
            remaining_errors=[],
            localization_candidates=candidates,
            ground_truth_files=["App.kt"],
            ground_truth_source_sets={"App.kt": "common"},
        )

        assert m.bsr == 1.0
        assert m.ctsr == 1.0
        assert m.ffsr == 1.0
        assert m.efr == 1.0
        assert m.hit_at_1 == 1.0
        assert m.hit_at_3 == 1.0
        assert m.hit_at_5 == 1.0
        assert m.source_set_accuracy == 1.0

    def test_no_ground_truth_hit_none(self) -> None:
        m = compute_metrics(
            case_id="c2", repair_mode="raw_error",
            validation=None, original_errors=[], remaining_errors=[],
            localization_candidates=[], ground_truth_files=None,
        )
        assert m.hit_at_1 is None
        assert m.hit_at_3 is None
        assert m.hit_at_5 is None
        assert m.source_set_accuracy is None


# ---------------------------------------------------------------------------
# evaluate() — orchestrator (patched DB)
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

    bundle = CaseBundle(
        meta=CaseMeta(
            case_id="case-012",
            event_id="ev-12",
            repository_url="https://github.com/test/repo",
            status="VALIDATED",
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
            error_observations=[_err(line=10)],
        )
    )
    bundle.repair = RepairEvidence(
        localization=LocalizationResult(candidates=[
            LocalizationResult.Candidate(
                rank=1, file_path="App.kt", source_set="common",
                classification="shared_code", score=0.9,
            )
        ])
    )
    bundle.structural = StructuralEvidence(
        source_set_map=SourceSetMap(), total_kotlin_files=3,
    )
    bundle.validation = _validation([("shared", ValidationStatus.SUCCESS_REPOSITORY_LEVEL)])
    return bundle


class TestEvaluate:
    def test_upserts_metric_row(self) -> None:
        from kmp_repair_pipeline.evaluation.evaluator import evaluate

        bundle = _make_bundle()
        session = MagicMock()

        attempt_row = MagicMock()
        attempt_row.id = "attempt-001"
        attempt_row.attempt_number = 1
        attempt_row.repair_mode = "full_thesis"

        with (
            patch("kmp_repair_pipeline.evaluation.evaluator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.evaluation.evaluator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.evaluation.evaluator.EvaluationMetricRepo") as MockMetric,
            patch("kmp_repair_pipeline.evaluation.evaluator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.evaluation.evaluator.TaskResultRepo"),
            patch("kmp_repair_pipeline.evaluation.evaluator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.evaluation.evaluator.RepairCaseRepo") as MockCase,
        ):
            MockPatch.return_value.list_for_case.return_value = [attempt_row]
            vr = MagicMock()
            vr.target = "shared"
            vr.status = ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
            vr.unavailable_reason = None
            vr.duration_s = 1.0
            vr.execution_run_id = None
            MockValRun.return_value.list_for_patch.return_value = [vr]
            MockMetric.return_value.upsert.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()

            result = evaluate(case_id="case-012", session=session)

        assert len(result.metrics) == 1
        m = result.metrics[0]
        assert m.repair_mode == "full_thesis"
        assert m.bsr == 1.0
        MockMetric.return_value.upsert.assert_called_once()

    def test_no_attempts_returns_empty(self) -> None:
        from kmp_repair_pipeline.evaluation.evaluator import evaluate

        bundle = _make_bundle()
        session = MagicMock()

        with (
            patch("kmp_repair_pipeline.evaluation.evaluator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.evaluation.evaluator.PatchAttemptRepo") as MockPatch,
        ):
            MockPatch.return_value.list_for_case.return_value = []
            result = evaluate(case_id="case-012", session=session)

        assert result.metrics == []

    def test_case_not_found_raises(self) -> None:
        from kmp_repair_pipeline.evaluation.evaluator import evaluate

        with patch("kmp_repair_pipeline.evaluation.evaluator.from_db_case", return_value=None):
            with pytest.raises(ValueError, match="not found"):
                evaluate(case_id="bad-id", session=MagicMock())

    def test_ground_truth_wires_to_metrics(self) -> None:
        from kmp_repair_pipeline.evaluation.evaluator import evaluate

        bundle = _make_bundle()
        session = MagicMock()

        attempt_row = MagicMock()
        attempt_row.id = "attempt-001"
        attempt_row.attempt_number = 1
        attempt_row.repair_mode = "raw_error"

        with (
            patch("kmp_repair_pipeline.evaluation.evaluator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.evaluation.evaluator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.evaluation.evaluator.EvaluationMetricRepo") as MockMetric,
            patch("kmp_repair_pipeline.evaluation.evaluator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.evaluation.evaluator.TaskResultRepo"),
            patch("kmp_repair_pipeline.evaluation.evaluator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.evaluation.evaluator.RepairCaseRepo") as MockCase,
        ):
            MockPatch.return_value.list_for_case.return_value = [attempt_row]
            vr = MagicMock()
            vr.target = "shared"
            vr.status = ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
            vr.unavailable_reason = None
            vr.duration_s = 1.0
            vr.execution_run_id = None
            MockValRun.return_value.list_for_patch.return_value = [vr]
            MockMetric.return_value.upsert.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()

            gt = {"changed_files": ["App.kt"], "source_sets": {"App.kt": "common"}}
            result = evaluate(case_id="case-012", session=session, ground_truth=gt)

        m = result.metrics[0]
        assert m.hit_at_1 == 1.0   # App.kt is rank-1 candidate
        assert m.source_set_accuracy == 1.0

    def test_validation_is_scoped_per_baseline_attempt(self) -> None:
        from kmp_repair_pipeline.evaluation.evaluator import evaluate

        bundle = _make_bundle()
        session = MagicMock()

        raw_attempt = MagicMock()
        raw_attempt.id = "attempt-raw"
        raw_attempt.attempt_number = 1
        raw_attempt.repair_mode = "raw_error"

        thesis_attempt = MagicMock()
        thesis_attempt.id = "attempt-thesis"
        thesis_attempt.attempt_number = 1
        thesis_attempt.repair_mode = "full_thesis"

        failed_vr = MagicMock()
        failed_vr.target = "shared"
        failed_vr.status = ValidationStatus.FAILED_BUILD.value
        failed_vr.unavailable_reason = None
        failed_vr.duration_s = 1.0
        failed_vr.execution_run_id = None

        success_vr = MagicMock()
        success_vr.target = "shared"
        success_vr.status = ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value
        success_vr.unavailable_reason = None
        success_vr.duration_s = 1.0
        success_vr.execution_run_id = None

        with (
            patch("kmp_repair_pipeline.evaluation.evaluator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.evaluation.evaluator.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.evaluation.evaluator.EvaluationMetricRepo") as MockMetric,
            patch("kmp_repair_pipeline.evaluation.evaluator.ValidationRunRepo") as MockValRun,
            patch("kmp_repair_pipeline.evaluation.evaluator.TaskResultRepo"),
            patch("kmp_repair_pipeline.evaluation.evaluator.ErrorObservationRepo"),
            patch("kmp_repair_pipeline.evaluation.evaluator.RepairCaseRepo") as MockCase,
        ):
            MockPatch.return_value.list_for_case.return_value = [raw_attempt, thesis_attempt]
            # Two calls per mode: remaining errors loader + validation loader
            MockValRun.return_value.list_for_patch.side_effect = [
                [], [failed_vr],
                [], [success_vr],
            ]
            MockMetric.return_value.upsert.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()

            result = evaluate(case_id="case-012", session=session)

        by_mode = {m.repair_mode: m for m in result.metrics}
        assert by_mode["raw_error"].bsr == 0.0
        assert by_mode["full_thesis"].bsr == 1.0


class TestNoOpAutoScoring:
    """When after-state has 0 errors, evaluator auto-scores all modes BSR=1."""

    def test_no_original_errors_auto_scores_all_modes(self) -> None:
        from kmp_repair_pipeline.evaluation.evaluator import evaluate
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
        from kmp_repair_pipeline.case_bundle.evidence import (
            ExecutionEvidence, RevisionExecution,
        )

        # Bundle with 0 after-state errors
        bundle = CaseBundle(
            meta=CaseMeta(
                case_id="no-op-case",
                event_id="ev-1",
                repository_url="https://github.com/test/repo",
                status="EXECUTED",
            )
        )
        after_exec = RevisionExecution(
            revision_type="after",
            profile="macos-full",
            overall_status=ValidationStatus.SUCCESS_REPOSITORY_LEVEL,
            task_outcomes=[],
            error_observations=[],
            env_metadata={},
        )
        bundle.execution = ExecutionEvidence(before=None, after=after_exec)

        session = MagicMock()

        with (
            patch("kmp_repair_pipeline.evaluation.evaluator.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.evaluation.evaluator.EvaluationMetricRepo") as MockMetric,
            patch("kmp_repair_pipeline.evaluation.evaluator.RepairCaseRepo") as MockCase,
        ):
            MockMetric.return_value.upsert.return_value = MagicMock()
            MockCase.return_value.get_by_id.return_value = MagicMock()

            result = evaluate(case_id="no-op-case", session=session)

        # Should produce metrics for all 4 baseline modes
        assert len(result.metrics) == 4
        for m in result.metrics:
            assert m.bsr == 1.0
            assert m.ctsr == 1.0
            assert m.ffsr == 1.0
            assert m.efr is None
            assert m.efr_normalized is None
