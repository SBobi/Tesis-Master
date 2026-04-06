"""Unit tests for Phase 6 runners — env_detector, error_parser, gradle_runner."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.runners.env_detector import EnvProfile, _compute_runnable_targets
from kmp_repair_pipeline.runners.error_parser import (
    determine_status_from_output,
    parse,
)
from kmp_repair_pipeline.runners.gradle_runner import tasks_for_target


# ---------------------------------------------------------------------------
# env_detector — _compute_runnable_targets
# ---------------------------------------------------------------------------


class TestComputeRunnableTargets:
    def _profile(self, **kwargs) -> EnvProfile:
        defaults = dict(
            java_available=True,
            gradlew_available=True,
            android_sdk_available=False,
            xcode_available=False,
            is_macos=False,
        )
        defaults.update(kwargs)
        p = EnvProfile(**defaults)
        _compute_runnable_targets(p)
        return p

    def test_shared_always_runnable_with_java_and_gradlew(self) -> None:
        p = self._profile()
        assert "shared" in p.runnable_targets

    def test_android_runnable_when_sdk_present(self) -> None:
        p = self._profile(android_sdk_available=True)
        assert "android" in p.runnable_targets
        assert "android" not in p.unavailable_targets

    def test_android_unavailable_without_sdk(self) -> None:
        p = self._profile(android_sdk_available=False)
        assert "android" not in p.runnable_targets
        assert "android" in p.unavailable_targets

    def test_ios_runnable_on_macos_with_xcode(self) -> None:
        p = self._profile(is_macos=True, xcode_available=True)
        assert "ios" in p.runnable_targets

    def test_ios_unavailable_on_linux(self) -> None:
        p = self._profile(is_macos=False, xcode_available=False)
        assert "ios" not in p.runnable_targets
        assert "ios" in p.unavailable_targets
        assert "macOS" in p.unavailable_targets["ios"]

    def test_ios_unavailable_on_macos_without_xcode(self) -> None:
        p = self._profile(is_macos=True, xcode_available=False)
        assert "ios" in p.unavailable_targets
        assert "Xcode" in p.unavailable_targets["ios"]

    def test_no_java_marks_all_unavailable(self) -> None:
        p = self._profile(java_available=False)
        assert not p.runnable_targets
        assert "shared" in p.unavailable_targets
        assert "android" in p.unavailable_targets

    def test_no_gradlew_marks_all_unavailable(self) -> None:
        p = self._profile(gradlew_available=False)
        assert not p.runnable_targets
        assert "shared" in p.unavailable_targets


# ---------------------------------------------------------------------------
# error_parser — parse
# ---------------------------------------------------------------------------


KOTLIN_ERRORS = textwrap.dedent("""\
    > Task :compileCommonMainKotlinMetadata FAILED
    e: /home/user/project/src/commonMain/kotlin/App.kt: (42, 10): error: Unresolved reference: HttpClient
    e: /home/user/project/src/commonMain/kotlin/App.kt: (55, 5): error: Overload resolution ambiguity
    e: /home/user/project/src/androidMain/kotlin/Platform.kt: (12, 1): error: None of the following candidates is applicable
""")

SIMPLE_FORMAT_ERRORS = textwrap.dedent("""\
    e: src/commonMain/kotlin/Foo.kt:10:4: error: Unresolved reference: Bar
""")

DEPENDENCY_ERROR = textwrap.dedent("""\
    > Could not resolve io.ktor:ktor-client-core:3.4.1.
      Required by:
          project :shared
""")

AAPT_ERROR = textwrap.dedent("""\
    app/src/main/res/layout/activity_main.xml:5: error: attribute android:text not found
""")


class TestErrorParser:
    def test_parses_kotlin_file_line_column_errors(self) -> None:
        errors = parse(KOTLIN_ERRORS, "")
        file_errors = [e for e in errors if e.file_path and "App.kt" in e.file_path]
        assert len(file_errors) >= 2

    def test_extracts_correct_line_numbers(self) -> None:
        errors = parse(KOTLIN_ERRORS, "")
        lines = {e.line for e in errors if e.file_path and "App.kt" in e.file_path}
        assert 42 in lines
        assert 55 in lines

    def test_parses_simple_format(self) -> None:
        errors = parse(SIMPLE_FORMAT_ERRORS, "")
        assert any(e.line == 10 and "Bar" in (e.message or "") for e in errors)

    def test_parses_dependency_error(self) -> None:
        errors = parse(DEPENDENCY_ERROR, "")
        dep_errors = [e for e in errors if e.error_type == "DEPENDENCY_RESOLUTION_ERROR"]
        assert len(dep_errors) >= 1
        assert "ktor" in dep_errors[0].message.lower()

    def test_parses_aapt_xml_error(self) -> None:
        errors = parse(AAPT_ERROR, "")
        xml_errors = [e for e in errors if e.error_type == "RESOURCE_ERROR"]
        assert len(xml_errors) >= 1
        assert xml_errors[0].line == 5

    def test_deduplicates_errors(self) -> None:
        doubled = KOTLIN_ERRORS + "\n" + KOTLIN_ERRORS
        errors = parse(doubled, "")
        # Should not double-count same file/line/message
        seen = set()
        for e in errors:
            key = f"{e.file_path}|{e.line}|{e.message}"
            assert key not in seen
            seen.add(key)

    def test_error_type_is_compile_error_for_kotlin(self) -> None:
        errors = parse(KOTLIN_ERRORS, "")
        kotlin_errors = [e for e in errors if e.file_path and ".kt" in (e.file_path or "")]
        assert all(e.error_type == "COMPILE_ERROR" for e in kotlin_errors)

    def test_empty_output_returns_empty_list(self) -> None:
        assert parse("", "") == []

    def test_parser_label_is_set(self) -> None:
        errors = parse(KOTLIN_ERRORS, "", parser_label="test-parser")
        assert all(e.parser == "test-parser" for e in errors)


class TestDetermineStatus:
    def test_exit_0_is_success(self) -> None:
        status = determine_status_from_output(0, "BUILD SUCCESSFUL", "")
        assert status == "SUCCESS_REPOSITORY_LEVEL"

    def test_exit_1_with_compile_errors_is_failed_build(self) -> None:
        status = determine_status_from_output(1, KOTLIN_ERRORS, "")
        assert status == "FAILED_BUILD"

    def test_exit_1_with_resolve_error_is_failed_build(self) -> None:
        status = determine_status_from_output(1, DEPENDENCY_ERROR, "")
        assert status == "FAILED_BUILD"

    def test_exit_0_with_test_failures(self) -> None:
        status = determine_status_from_output(0, "3 tests failed", "")
        assert status == "FAILED_TESTS"


# ---------------------------------------------------------------------------
# gradle_runner — tasks_for_target
# ---------------------------------------------------------------------------


class TestTasksForTarget:
    def test_shared_returns_compile_tasks(self) -> None:
        tasks = tasks_for_target("shared")
        assert any("compile" in t.lower() or "Compile" in t for t in tasks)

    def test_android_returns_assemble(self) -> None:
        tasks = tasks_for_target("android")
        assert any("assemble" in t.lower() or "Assemble" in t for t in tasks)

    def test_ios_returns_ios_compile(self) -> None:
        tasks = tasks_for_target("ios")
        assert any("ios" in t.lower() or "Ios" in t for t in tasks)

    def test_unknown_target_returns_build(self) -> None:
        tasks = tasks_for_target("unknown-target")
        assert "build" in tasks


# ---------------------------------------------------------------------------
# gradle_runner — run_tasks (patched subprocess)
# ---------------------------------------------------------------------------


class TestRunTasks:
    def test_returns_result_per_task(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.gradle_runner import run_tasks

        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\necho 'BUILD SUCCESSFUL'\nexit 0\n")
        gradlew.chmod(0o755)

        results = run_tasks(tmp_path, ["help"], timeout_s=30)
        assert len(results) == 1
        assert results[0].task_name == "help"
        assert results[0].exit_code == 0

    def test_captures_stdout(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.gradle_runner import run_tasks

        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\necho 'HELLO WORLD'\nexit 0\n")
        gradlew.chmod(0o755)

        results = run_tasks(tmp_path, ["tasks"], timeout_s=30)
        assert "HELLO WORLD" in results[0].stdout

    def test_nonzero_exit_sets_failed_status(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.gradle_runner import run_tasks

        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\necho 'e: File.kt: (1, 1): error: bad'\nexit 1\n")
        gradlew.chmod(0o755)

        results = run_tasks(tmp_path, ["compile"], timeout_s=30)
        assert results[0].exit_code == 1
        assert results[0].status == "FAILED_BUILD"

    def test_missing_gradlew_raises(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.gradle_runner import run_tasks

        with pytest.raises(FileNotFoundError):
            run_tasks(tmp_path / "no-repo", ["build"])

    def test_env_extra_is_forwarded_to_subprocess(self, tmp_path: Path) -> None:
        """env_extra dict is merged into subprocess environment."""
        from kmp_repair_pipeline.runners.gradle_runner import run_tasks

        # gradlew prints the value of JAVA_HOME_TEST from its env
        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\necho \"JAVA=$JAVA_HOME_TEST\"\nexit 0\n")
        gradlew.chmod(0o755)

        results = run_tasks(tmp_path, ["tasks"], timeout_s=30,
                            env_extra={"JAVA_HOME_TEST": "sentinel-value"})
        assert "sentinel-value" in results[0].stdout


class TestLocalPropertiesHelper:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.env_detector import _write_local_properties

        _write_local_properties(tmp_path, "/some/sdk")
        content = (tmp_path / "local.properties").read_text()
        assert "sdk.dir=/some/sdk" in content

    def test_write_idempotent(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.env_detector import _write_local_properties

        _write_local_properties(tmp_path, "/some/sdk")
        _write_local_properties(tmp_path, "/some/sdk")
        content = (tmp_path / "local.properties").read_text()
        assert content.count("sdk.dir=") == 1

    def test_write_updates_stale_value(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.runners.env_detector import _write_local_properties

        (tmp_path / "local.properties").write_text("sdk.dir=/old/sdk\n")
        _write_local_properties(tmp_path, "/new/sdk")
        content = (tmp_path / "local.properties").read_text()
        assert "/new/sdk" in content
        assert "/old/sdk" not in content


class TestNoOpDetectionInExecution:
    def test_zero_errors_sets_no_errors_to_fix_status(self) -> None:
        """run_before_after sets case status to NO_ERRORS_TO_FIX when after has 0 errors."""
        from kmp_repair_pipeline.runners.execution_runner import run_before_after
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta

        bundle = CaseBundle(
            meta=CaseMeta(
                case_id="no-err-case",
                event_id="ev-1",
                repository_url="https://github.com/test/repo",
                status="SHADOW_BUILT",
            )
        )

        set_status_calls: list[str] = []

        def fake_set_status(case_row, status: str) -> None:
            set_status_calls.append(status)

        session = MagicMock()

        with (
            patch("kmp_repair_pipeline.runners.execution_runner.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.runners.execution_runner.RevisionRepo") as MockRev,
            patch("kmp_repair_pipeline.runners.execution_runner.detect") as MockDetect,
            patch("kmp_repair_pipeline.runners.execution_runner._run_revision",
                  return_value=([], [])),
            patch("kmp_repair_pipeline.runners.execution_runner.to_db"),
            patch("kmp_repair_pipeline.runners.execution_runner.RepairCaseRepo") as MockCase,
            patch("kmp_repair_pipeline.runners.execution_runner._reset_workspace_path"),
            patch("kmp_repair_pipeline.runners.execution_runner._write_local_properties"),
        ):
            rev_mock = MagicMock()
            rev_mock.local_path = "/tmp/after"
            MockRev.return_value.get.return_value = rev_mock
            env = MagicMock()
            env.is_macos = True
            env.java_home = ""
            env.runnable_targets = ["shared"]
            env.unavailable_targets = {}
            env.android_sdk_available = False
            env.as_metadata_dict.return_value = {}
            MockDetect.return_value = env
            MockCase.return_value.get_by_id.return_value = MagicMock()
            MockCase.return_value.set_status.side_effect = fake_set_status

            run_before_after("no-err-case", session)

        assert "NO_ERRORS_TO_FIX" in set_status_calls
