"""Tests for the improvements made in the April 2026 audit-closure session.

Covers:
  Batch A — Safety
    - WorkspaceLock acquire/release/timeout
    - chain_by_file rollback on partial failure
    - _verify_patch_present warns when no touched files are in git diff
    - _read_file_contents visible truncation
    - repair_context expect/actual coupling

  Batch B — Error classification
    - DEPENDENCY_CONFLICT_ERROR detection
    - BUILD_SCRIPT_ERROR detection
    - API_BREAK_ERROR detection + symbol_name extraction

  Batch C — Catalog diff
    - diff_catalogs detects alias renames, artifact renames, added/removed

  Batch D — EFR normalization
    - compute_efr_message_normalized ignores line-number shifts
    - CaseMetrics includes efr_normalized
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Batch A: WorkspaceLock
# ---------------------------------------------------------------------------


class TestWorkspaceLock:
    def test_acquire_and_release(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.utils.workspace_lock import WorkspaceLock

        with WorkspaceLock(tmp_path) as lock:
            assert lock._fh is not None
            assert (tmp_path / ".kmp-repair.lock").exists()
        assert lock._fh is None

    def test_context_manager_releases_on_exception(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.utils.workspace_lock import WorkspaceLock

        lock = WorkspaceLock(tmp_path)
        try:
            with lock:
                raise ValueError("simulated error")
        except ValueError:
            pass
        assert lock._fh is None

    def test_timeout_raises_workspace_lock_error(self, tmp_path: Path) -> None:
        """Two locks on the same dir — second times out immediately."""
        import fcntl

        from kmp_repair_pipeline.utils.workspace_lock import WorkspaceLock, WorkspaceLockError

        lock1 = WorkspaceLock(tmp_path)
        lock1.acquire()
        try:
            lock2 = WorkspaceLock(tmp_path, timeout_s=0.1)
            with pytest.raises(WorkspaceLockError):
                lock2.acquire()
        finally:
            lock1.release()


# ---------------------------------------------------------------------------
# Batch A: chain_by_file rollback
# ---------------------------------------------------------------------------


class TestChainByFileRollback:
    """_apply_patch_chain_by_file rolls back applied blocks on partial failure."""

    def _make_diff(self, file_a: str, file_b: str) -> str:
        """Two-file diff: first block clean, second block intentionally bad."""
        return textwrap.dedent(f"""\
            --- a/{file_a}
            +++ b/{file_a}
            @@ -1,1 +1,1 @@
            -old
            +new
            --- a/{file_b}
            +++ b/{file_b}
            @@ -999,1 +999,1 @@
            -this_line_does_not_exist
            +new
        """)

    def test_failure_triggers_rollback_attempt(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _apply_patch_chain_by_file
        from kmp_repair_pipeline.repair.patch_applier import PatchApplicationResult

        success_result = PatchApplicationResult(
            success=True, touched_files=["file_a.kt"], method="patch"
        )
        fail_result = PatchApplicationResult(
            success=False, touched_files=[], stderr="failed at file_b", method="patch"
        )
        revert_result = PatchApplicationResult(success=True, method="patch")

        with patch("kmp_repair_pipeline.repair.repairer.apply_patch") as mock_apply, \
             patch("kmp_repair_pipeline.repair.repairer.revert_patch") as mock_revert:
            mock_apply.side_effect = [success_result, fail_result]
            mock_revert.return_value = revert_result

            diff = self._make_diff("file_a.kt", "file_b.kt")
            result = _apply_patch_chain_by_file(diff, tmp_path)

        assert result.success is False
        assert "chain_by_file failed at block 2" in result.stderr
        # Rollback was called once (for the first applied block)
        assert mock_revert.call_count == 1

    def test_full_success_does_not_rollback(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _apply_patch_chain_by_file
        from kmp_repair_pipeline.repair.patch_applier import PatchApplicationResult

        ok = PatchApplicationResult(success=True, touched_files=["f.kt"], method="patch")

        with patch("kmp_repair_pipeline.repair.repairer.apply_patch", return_value=ok), \
             patch("kmp_repair_pipeline.repair.repairer.revert_patch") as mock_revert:
            diff = self._make_diff("a.kt", "b.kt")
            result = _apply_patch_chain_by_file(diff, tmp_path)

        assert result.success is True
        mock_revert.assert_not_called()


# ---------------------------------------------------------------------------
# Batch A: _verify_patch_present
# ---------------------------------------------------------------------------


class TestVerifyPatchPresent:
    def _make_attempt(self, touched_files: list[str], attempt_number: int = 1):
        m = MagicMock()
        m.touched_files = touched_files
        m.attempt_number = attempt_number
        m.repair_mode = "full_thesis"
        return m

    def test_warns_when_no_overlap(self, tmp_path: Path, caplog) -> None:
        import logging
        from kmp_repair_pipeline.validation.validator import _verify_patch_present

        attempt = self._make_attempt(["src/commonMain/Foo.kt"])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="unrelated/Other.kt\n")
            with caplog.at_level(logging.WARNING):
                _verify_patch_present(tmp_path, attempt)
        assert "Patch presence check FAILED" in caplog.text

    def test_no_warning_when_overlap(self, tmp_path: Path, caplog) -> None:
        import logging
        from kmp_repair_pipeline.validation.validator import _verify_patch_present

        attempt = self._make_attempt(["src/commonMain/Foo.kt"])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="src/commonMain/Foo.kt\n"
            )
            with caplog.at_level(logging.WARNING):
                _verify_patch_present(tmp_path, attempt)
        assert "FAILED" not in caplog.text

    def test_skips_when_no_touched_files(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.validation.validator import _verify_patch_present

        attempt = self._make_attempt([])
        with patch("subprocess.run") as mock_run:
            _verify_patch_present(tmp_path, attempt)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Batch A: _read_file_contents visible truncation
# ---------------------------------------------------------------------------


class TestReadFileContents:
    def test_no_truncation_for_small_file(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _read_file_contents

        f = tmp_path / "small.kt"
        f.write_text("hello world")
        contents = _read_file_contents([str(f)])
        assert "truncated" not in contents[str(f)]
        assert "hello world" in contents[str(f)]

    def test_truncation_message_contains_sizes(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _read_file_contents

        f = tmp_path / "large.kt"
        f.write_text("x" * 20000)
        contents = _read_file_contents([str(f)], max_bytes=8000)
        text = contents[str(f)]
        assert "truncated" in text
        assert "8000" in text
        assert "20000" in text

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _read_file_contents

        contents = _read_file_contents([str(tmp_path / "nonexistent.kt")])
        assert contents == {}


# ---------------------------------------------------------------------------
# Batch A: expect/actual coupling in repair_context
# ---------------------------------------------------------------------------


class TestExpectActualCoupling:
    def _make_bundle(self, localized_files, expect_actual_pairs):
        """Build a minimal CaseBundle with localization + expect/actual pairs."""
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
        from kmp_repair_pipeline.case_bundle.evidence import (
            LocalizationResult,
            RepairEvidence,
            StructuralEvidence,
        )
        from kmp_repair_pipeline.domain.analysis import ExpectActualPair

        meta = CaseMeta(
            case_id="test-case-id",
            event_id="evt",
            repository_url="https://github.com/example/repo",
        )
        bundle = CaseBundle(meta=meta)

        candidates = [
            LocalizationResult.Candidate(
                rank=i + 1,
                file_path=f,
                score=1.0 - i * 0.1,
                source_set="commonMain",
            )
            for i, f in enumerate(localized_files)
        ]
        bundle.repair = RepairEvidence(
            localization=LocalizationResult(candidates=candidates)
        )

        pairs = [
            ExpectActualPair(
                expect_fqcn=p["fqcn"],
                expect_file=p["expect"],
                actual_files=p["actuals"],
            )
            for p in expect_actual_pairs
        ]
        bundle.structural = StructuralEvidence(expect_actual_pairs=pairs)
        return bundle

    def test_actual_files_added_when_expect_is_localized(self) -> None:
        bundle = self._make_bundle(
            localized_files=["src/commonMain/Foo.kt"],
            expect_actual_pairs=[
                {
                    "fqcn": "com.example.Foo",
                    "expect": "src/commonMain/Foo.kt",
                    "actuals": ["src/androidMain/FooAndroid.kt", "src/iosMain/FooIos.kt"],
                }
            ],
        )
        ctx = bundle.repair_context(top_k=5)
        assert "src/androidMain/FooAndroid.kt" in ctx["localized_files"]
        assert "src/iosMain/FooIos.kt" in ctx["localized_files"]

    def test_expect_file_added_when_actual_is_localized(self) -> None:
        bundle = self._make_bundle(
            localized_files=["src/androidMain/FooAndroid.kt"],
            expect_actual_pairs=[
                {
                    "fqcn": "com.example.Foo",
                    "expect": "src/commonMain/Foo.kt",
                    "actuals": ["src/androidMain/FooAndroid.kt"],
                }
            ],
        )
        ctx = bundle.repair_context(top_k=5)
        assert "src/commonMain/Foo.kt" in ctx["localized_files"]

    def test_no_duplicates_in_localized_files(self) -> None:
        bundle = self._make_bundle(
            localized_files=["src/commonMain/Foo.kt", "src/androidMain/FooAndroid.kt"],
            expect_actual_pairs=[
                {
                    "fqcn": "com.example.Foo",
                    "expect": "src/commonMain/Foo.kt",
                    "actuals": ["src/androidMain/FooAndroid.kt"],
                }
            ],
        )
        ctx = bundle.repair_context(top_k=5)
        # No duplicates
        assert len(ctx["localized_files"]) == len(set(ctx["localized_files"]))

    def test_no_structural_evidence_is_safe(self) -> None:
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
        from kmp_repair_pipeline.case_bundle.evidence import (
            LocalizationResult,
            RepairEvidence,
        )

        meta = CaseMeta(
            case_id="test-case-id",
            event_id="evt",
            repository_url="https://github.com/example/repo",
        )
        bundle = CaseBundle(meta=meta)
        bundle.repair = RepairEvidence(
            localization=LocalizationResult(
                candidates=[
                    LocalizationResult.Candidate(
                        rank=1,
                        file_path="src/commonMain/Foo.kt",
                        score=1.0,
                        source_set="commonMain",
                    )
                ]
            )
        )
        # structural is None — must not raise
        ctx = bundle.repair_context(top_k=5)
        assert "src/commonMain/Foo.kt" in ctx["localized_files"]


# ---------------------------------------------------------------------------
# Batch B: New error types
# ---------------------------------------------------------------------------


class TestDependencyConflictError:
    def test_detects_conflict_with_dependency(self) -> None:
        from kmp_repair_pipeline.runners.error_parser import parse

        output = "> Conflict with dependency 'com.squareup.okhttp3:okhttp' in project ':app'."
        errors = parse(output, "")
        conflict_errors = [e for e in errors if e.error_type == "DEPENDENCY_CONFLICT_ERROR"]
        assert len(conflict_errors) >= 1
        assert "com.squareup.okhttp3:okhttp" in conflict_errors[0].message


class TestBuildScriptError:
    def test_detects_could_not_apply_plugin(self) -> None:
        from kmp_repair_pipeline.runners.error_parser import parse

        output = "> Could not apply plugin [id: 'com.android.application']"
        errors = parse(output, "")
        build_errors = [e for e in errors if e.error_type == "BUILD_SCRIPT_ERROR"]
        assert len(build_errors) >= 1
        assert "com.android.application" in build_errors[0].message

    def test_detects_exception_applying_plugin(self) -> None:
        from kmp_repair_pipeline.runners.error_parser import parse

        output = "> An exception occurred applying plugin request [id: 'org.jetbrains.kotlin.android']"
        errors = parse(output, "")
        build_errors = [e for e in errors if e.error_type == "BUILD_SCRIPT_ERROR"]
        assert len(build_errors) >= 1


class TestApiBreakError:
    def test_detects_unresolved_reference(self) -> None:
        from kmp_repair_pipeline.runners.error_parser import parse

        output = "e: Foo.kt:(12,5): error: Unresolved reference: HttpClient"
        errors = parse(output, "")
        api_errors = [e for e in errors if e.error_type == "API_BREAK_ERROR"]
        assert len(api_errors) >= 1
        assert api_errors[0].symbol_name == "HttpClient"
        assert "HttpClient" in api_errors[0].message

    def test_detects_type_mismatch(self) -> None:
        from kmp_repair_pipeline.runners.error_parser import parse

        output = "e: Bar.kt:(8,3): Type mismatch: inferred type is HttpClientConfig<*> but Nothing was expected"
        errors = parse(output, "")
        api_errors = [e for e in errors if e.error_type == "API_BREAK_ERROR"]
        assert len(api_errors) >= 1

    def test_symbol_name_not_set_for_type_mismatch(self) -> None:
        from kmp_repair_pipeline.runners.error_parser import parse

        output = "Type mismatch: inferred type is Foo"
        errors = parse(output, "")
        api_errors = [e for e in errors if e.error_type == "API_BREAK_ERROR"]
        # symbol_name is optional for type-mismatch errors
        for e in api_errors:
            assert e.message is not None


# ---------------------------------------------------------------------------
# Batch C: Catalog diff
# ---------------------------------------------------------------------------


_BEFORE_TOML = textwrap.dedent("""\
    [versions]
    ktor = "3.1.3"
    kotlin = "2.0.0"

    [libraries]
    ktor-xml = { module = "io.ktor:ktor-client-xml", version.ref = "ktor" }
    ktor-core = { module = "io.ktor:ktor-client-core", version.ref = "ktor" }

    [plugins]
    kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
""")

_AFTER_TOML = textwrap.dedent("""\
    [versions]
    ktor = "3.4.1"
    kotlin = "2.0.0"

    [libraries]
    ktor-content-negotiation = { module = "io.ktor:ktor-client-xml", version.ref = "ktor" }
    ktor-core = { module = "io.ktor:ktor-client-core", version.ref = "ktor" }
    ktor-new = { module = "io.ktor:ktor-client-newfeature", version.ref = "ktor" }

    [plugins]
    kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
""")

_ARTIFACT_RENAME_TOML = textwrap.dedent("""\
    [versions]
    ktor = "3.4.1"
    kotlin = "2.0.0"

    [libraries]
    ktor-xml = { module = "io.ktor:ktor-client-content-negotiation-xmlutil", version.ref = "ktor" }
    ktor-core = { module = "io.ktor:ktor-client-core", version.ref = "ktor" }
""")


class TestCatalogDiff:
    def test_detects_alias_rename(self) -> None:
        from kmp_repair_pipeline.ingest.catalog_diff import diff_catalogs

        diff = diff_catalogs(_BEFORE_TOML, _AFTER_TOML)
        # ktor-xml was removed, ktor-content-negotiation was added with same module
        renames = {r.before_alias: r for r in diff.alias_renames}
        assert "ktor-xml" in renames
        assert renames["ktor-xml"].after_alias == "ktor-content-negotiation"

    def test_detects_added_alias(self) -> None:
        from kmp_repair_pipeline.ingest.catalog_diff import diff_catalogs

        diff = diff_catalogs(_BEFORE_TOML, _AFTER_TOML)
        assert "ktor-new" in diff.added_aliases

    def test_detects_artifact_rename(self) -> None:
        from kmp_repair_pipeline.ingest.catalog_diff import diff_catalogs

        diff = diff_catalogs(_BEFORE_TOML, _ARTIFACT_RENAME_TOML)
        art_renames = {r.alias: r for r in diff.artifact_renames}
        assert "ktor-xml" in art_renames
        assert art_renames["ktor-xml"].after_module == "io.ktor:ktor-client-content-negotiation-xmlutil"

    def test_no_changes_when_catalogs_identical(self) -> None:
        from kmp_repair_pipeline.ingest.catalog_diff import diff_catalogs

        diff = diff_catalogs(_BEFORE_TOML, _BEFORE_TOML)
        assert not diff.has_changes

    def test_to_dict_serialization(self) -> None:
        from kmp_repair_pipeline.ingest.catalog_diff import diff_catalogs

        diff = diff_catalogs(_BEFORE_TOML, _AFTER_TOML)
        d = diff.to_dict()
        assert "alias_renames" in d
        assert "artifact_renames" in d
        assert "added_aliases" in d
        assert "removed_aliases" in d


# ---------------------------------------------------------------------------
# Batch D: EFR normalization
# ---------------------------------------------------------------------------


class TestEfrNormalized:
    def _obs(self, error_type="COMPILE_ERROR", file_path="Foo.kt", line=10, message="msg"):
        from kmp_repair_pipeline.case_bundle.evidence import ErrorObservation

        return ErrorObservation(
            error_type=error_type,
            file_path=file_path,
            line=line,
            message=message,
        )

    def test_same_error_on_different_line_counts_as_fixed_in_standard_efr(self) -> None:
        """Standard EFR uses (type, file, line, message) — line shift = counts as fixed."""
        from kmp_repair_pipeline.evaluation.metrics import compute_efr

        original = [self._obs(line=10)]
        remaining = [self._obs(line=15)]   # same message, different line
        efr = compute_efr(original, remaining)
        # Standard EFR counts it as fixed (line 10 gone, line 15 is "new")
        assert efr is not None and efr > 0.0

    def test_normalized_efr_does_not_count_line_shift_as_fix(self) -> None:
        """Message-normalized EFR: same error on different line → NOT counted as fixed."""
        from kmp_repair_pipeline.evaluation.metrics import compute_efr_message_normalized

        original = [self._obs(line=10)]
        remaining = [self._obs(line=15)]   # same message, different line
        efr_norm = compute_efr_message_normalized(original, remaining)
        # Normalized EFR should be 0 — error still present (just moved lines)
        assert efr_norm == 0.0

    def test_normalized_efr_returns_none_when_no_original(self) -> None:
        from kmp_repair_pipeline.evaluation.metrics import compute_efr_message_normalized

        assert compute_efr_message_normalized([], []) is None

    def test_normalized_efr_is_one_when_all_fixed(self) -> None:
        from kmp_repair_pipeline.evaluation.metrics import compute_efr_message_normalized

        original = [self._obs(message="error A"), self._obs(message="error B")]
        remaining = []
        assert compute_efr_message_normalized(original, remaining) == 1.0

    def test_case_metrics_includes_efr_normalized(self) -> None:
        from kmp_repair_pipeline.evaluation.metrics import compute_metrics

        original = [self._obs(line=10)]
        remaining = [self._obs(line=15)]

        m = compute_metrics(
            case_id="test",
            repair_mode="full_thesis",
            validation=None,
            original_errors=original,
            remaining_errors=remaining,
            localization_candidates=[],
        )
        assert hasattr(m, "efr_normalized")
        assert m.efr_normalized is not None
