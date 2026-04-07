"""Unit tests for Phase 9 — repair_agent, patch_applier, repairer, baselines."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.repair.patch_applier import (
    PatchApplicationResult,
    apply_patch,
    extract_touched_files,
)
from kmp_repair_pipeline.repair.repair_agent import (
    _build_prompt,
    _extract_touched_files,
    _format_errors,
)
from kmp_repair_pipeline.utils.llm_provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# repair_agent — _extract_touched_files
# ---------------------------------------------------------------------------

SAMPLE_DIFF = textwrap.dedent("""\
    --- a/src/commonMain/kotlin/App.kt
    +++ b/src/commonMain/kotlin/App.kt
    @@ -1,5 +1,5 @@
     package com.example
    -import io.ktor.client.HttpClient
    +import io.ktor.client.*
     class App
    --- a/src/androidMain/kotlin/Platform.kt
    +++ b/src/androidMain/kotlin/Platform.kt
    @@ -3,3 +3,4 @@
     actual fun platform(): String = "Android"
    +// updated
""")

MALFORMED_DIFF = textwrap.dedent("""\
    --- a/src/commonMain/kotlin/App.kt
    +++ b/src/commonMain/kotlin/App.kt
    @@ -1,3 +1,3 @@
     package com.example
    this line is malformed in a hunk
""")


class TestExtractTouchedFiles:
    def test_extracts_two_files(self) -> None:
        files = _extract_touched_files(SAMPLE_DIFF)
        assert "src/commonMain/kotlin/App.kt" in files
        assert "src/androidMain/kotlin/Platform.kt" in files

    def test_strips_b_prefix(self) -> None:
        files = _extract_touched_files(SAMPLE_DIFF)
        assert all(not f.startswith("b/") for f in files)

    def test_no_duplicates(self) -> None:
        doubled = SAMPLE_DIFF + SAMPLE_DIFF
        files = _extract_touched_files(doubled)
        assert len(files) == len(set(files))

    def test_empty_diff_returns_empty(self) -> None:
        assert _extract_touched_files("") == []

    def test_dev_null_excluded(self) -> None:
        diff = "+++ /dev/null\n"
        assert _extract_touched_files(diff) == []


# ---------------------------------------------------------------------------
# repair_agent — _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def _ctx(self, mode: str = "full_thesis") -> dict:
        return {
            "update": {
                "version_changes": [
                    {"dependency_group": "io.ktor", "before": "3.1.3", "after": "3.4.1"}
                ],
                "update_class": "direct_library",
            },
            "errors": [
                {"error_type": "COMPILE_ERROR", "file_path": "App.kt",
                 "line": 42, "message": "Unresolved reference: HttpClient"},
            ],
            "localized_files": ["src/commonMain/kotlin/App.kt"],
            "previous_attempts": [],
        }

    def test_raw_error_prompt_has_dep(self) -> None:
        p = _build_prompt(self._ctx(), attempt_number=1, mode="raw_error")
        assert "io.ktor" in p
        assert "3.1.3" in p

    def test_context_rich_includes_localized_files(self) -> None:
        p = _build_prompt(self._ctx(), attempt_number=1, mode="context_rich")
        assert "App.kt" in p

    def test_full_thesis_includes_previous_attempts(self) -> None:
        ctx = self._ctx()
        ctx["previous_attempts"] = [{"attempt": 1, "status": "FAILED_APPLY", "reason": "bad hunk"}]
        p = _build_prompt(ctx, attempt_number=2, mode="full_thesis")
        assert "FAILED_APPLY" in p
        assert "bad hunk" in p

    def test_iterative_agentic_same_as_full_thesis(self) -> None:
        ctx = self._ctx()
        p_full = _build_prompt(ctx, 1, "full_thesis")
        p_iter = _build_prompt(ctx, 1, "iterative_agentic")
        assert p_full == p_iter


# ---------------------------------------------------------------------------
# repair_agent — run_repair_agent (FakeLLM)
# ---------------------------------------------------------------------------


class TestRunRepairAgent:
    def test_returns_diff_from_llm(self) -> None:
        from kmp_repair_pipeline.repair.repair_agent import run_repair_agent

        provider = FakeLLMProvider(responses=[SAMPLE_DIFF])
        ctx = {
            "update": {"version_changes": [], "update_class": "direct_library"},
            "errors": [],
            "localized_files": [],
            "previous_attempts": [],
        }
        out = run_repair_agent(ctx, provider, attempt_number=1, repair_mode="full_thesis")
        assert out.diff_text == SAMPLE_DIFF.strip()
        assert not out.is_impossible
        assert any("App.kt" in f for f in out.touched_files)

    def test_patch_impossible_flag(self) -> None:
        from kmp_repair_pipeline.repair.repair_agent import run_repair_agent

        provider = FakeLLMProvider(responses=["PATCH_IMPOSSIBLE"])
        ctx = {"update": {"version_changes": [], "update_class": "?"}, "errors": [], "localized_files": [], "previous_attempts": []}
        out = run_repair_agent(ctx, provider)
        assert out.is_impossible
        assert out.touched_files == []

    def test_empty_response_is_impossible(self) -> None:
        from kmp_repair_pipeline.repair.repair_agent import run_repair_agent

        provider = FakeLLMProvider(responses=[""])
        ctx = {"update": {}, "errors": [], "localized_files": [], "previous_attempts": []}
        out = run_repair_agent(ctx, provider)
        assert out.is_impossible


# ---------------------------------------------------------------------------
# patch_applier — extract_touched_files
# ---------------------------------------------------------------------------


class TestExtractTouchedFilesPatchApplier:
    def test_matches_repair_agent_result(self) -> None:
        files = extract_touched_files(SAMPLE_DIFF)
        assert "src/commonMain/kotlin/App.kt" in files


# ---------------------------------------------------------------------------
# patch_applier — apply_patch (real filesystem, no network)
# ---------------------------------------------------------------------------


class TestApplyPatch:
    def _make_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        src = repo / "src" / "commonMain" / "kotlin"
        src.mkdir(parents=True)
        (src / "App.kt").write_text(
            "package com.example\nimport io.ktor.client.HttpClient\nclass App\n"
        )
        return repo

    def _make_valid_diff(self) -> str:
        return textwrap.dedent("""\
            --- a/src/commonMain/kotlin/App.kt
            +++ b/src/commonMain/kotlin/App.kt
            @@ -1,3 +1,3 @@
             package com.example
            -import io.ktor.client.HttpClient
            +import io.ktor.client.*
             class App
        """)

    def test_apply_returns_result(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        result = apply_patch(self._make_valid_diff(), repo)
        # Success depends on `patch` being available; just check it returns a result
        assert isinstance(result, PatchApplicationResult)
        assert isinstance(result.success, bool)

    def test_empty_diff_returns_failure(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        result = apply_patch("", repo)
        assert result.success is False

    def test_touched_files_from_diff_header(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        result = apply_patch(self._make_valid_diff(), repo)
        # Even if application fails, touched_files is derived from diff header
        if result.touched_files:
            assert any("App.kt" in f for f in result.touched_files)


# ---------------------------------------------------------------------------
# repairer — repair (patched DB + agent)
# ---------------------------------------------------------------------------


class TestRepair:
    def _make_bundle(self, case_id: str = "case-001"):
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
        from kmp_repair_pipeline.case_bundle.evidence import (
            ExecutionEvidence, LocalizationResult, RepairEvidence,
            RevisionExecution, StructuralEvidence, SourceSetMap, UpdateEvidence,
        )
        from kmp_repair_pipeline.domain.events import (
            DependencyUpdateEvent, UpdateClass, VersionChange,
        )
        from kmp_repair_pipeline.domain.validation import ValidationStatus

        bundle = CaseBundle(
            meta=CaseMeta(
                case_id=case_id, event_id="ev-1",
                repository_url="https://github.com/test/repo",
                status="LOCALIZED",
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
            localization=LocalizationResult(candidates=[
                LocalizationResult.Candidate(
                    rank=1, file_path="src/commonMain/kotlin/App.kt",
                    source_set="common", classification="shared_code", score=0.9,
                )
            ])
        )
        bundle.structural = StructuralEvidence(
            source_set_map=SourceSetMap(),
            total_kotlin_files=5,
        )
        return bundle

    def test_repair_sets_patch_attempted_status(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import repair

        bundle = self._make_bundle()
        provider = FakeLLMProvider(responses=[SAMPLE_DIFF])
        session = MagicMock()

        # Mock revision with a real tmp_path repo
        repo = tmp_path / "after"
        repo.mkdir()
        (repo / "src").mkdir()

        after_rev = MagicMock()
        after_rev.local_path = str(repo)

        with (
            patch("kmp_repair_pipeline.repair.repairer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.repair.repairer.to_db"),
            patch("kmp_repair_pipeline.repair.repairer.RevisionRepo") as MockRevRepo,
            patch("kmp_repair_pipeline.repair.repairer.PatchAttemptRepo") as MockPatchRepo,
            patch("kmp_repair_pipeline.repair.repairer.AgentLogRepo"),
            patch("kmp_repair_pipeline.repair.repairer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.repair.repairer.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.repair.repairer.apply_patch") as mock_apply,
        ):
            MockRevRepo.return_value.get.return_value = after_rev
            MockPatchRepo.return_value.list_for_case.return_value = []
            attempt_row = MagicMock()
            MockPatchRepo.return_value.create.return_value = attempt_row
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()
            store = MagicMock()
            store.write_prompt.return_value = ("/p/prompt.txt", "sha1")
            store.write_response.return_value = ("/p/response.txt", "sha2")
            store.write_patch.return_value = ("/p/patch.diff", "sha3")
            MockStore.return_value = store
            mock_apply.return_value = PatchApplicationResult(
                success=True, touched_files=["src/commonMain/kotlin/App.kt"]
            )

            result = repair(
                case_id="case-001",
                session=session,
                artifact_base=tmp_path / "artifacts",
                repair_mode="full_thesis",
                provider=provider,
            )

        assert bundle.meta.status == "PATCH_ATTEMPTED"
        assert result.patch_strategy == "single_diff"
        assert result.patch_status == "APPLIED"
        assert result.attempt_number == 1
        assert "src/commonMain/kotlin/App.kt" in result.touched_files
        assert attempt_row.retry_reason == "patch_strategy=single_diff"

    def test_impossible_patch_sets_impossible_status(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import repair

        bundle = self._make_bundle()
        provider = FakeLLMProvider(responses=["PATCH_IMPOSSIBLE"])
        session = MagicMock()

        after_rev = MagicMock()
        after_rev.local_path = str(tmp_path)

        with (
            patch("kmp_repair_pipeline.repair.repairer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.repair.repairer.to_db"),
            patch("kmp_repair_pipeline.repair.repairer.RevisionRepo") as MockRevRepo,
            patch("kmp_repair_pipeline.repair.repairer.PatchAttemptRepo") as MockPatchRepo,
            patch("kmp_repair_pipeline.repair.repairer.AgentLogRepo"),
            patch("kmp_repair_pipeline.repair.repairer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.repair.repairer.ArtifactStore") as MockStore,
        ):
            MockRevRepo.return_value.get.return_value = after_rev
            MockPatchRepo.return_value.list_for_case.return_value = []
            MockPatchRepo.return_value.create.return_value = MagicMock()
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()
            store = MagicMock()
            store.write_prompt.return_value = ("/p/prompt.txt", "sha1")
            store.write_response.return_value = ("/p/response.txt", "sha2")
            MockStore.return_value = store

            result = repair(
                case_id="case-001", session=session,
                artifact_base=tmp_path, repair_mode="raw_error",
                provider=provider,
                force_patch_attempt=False,
            )

        assert result.patch_strategy == "single_diff"
        assert result.patch_status == "IMPOSSIBLE"
        assert result.diff_path is None

    def test_malformed_diff_is_rejected_before_apply(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import repair

        bundle = self._make_bundle()
        provider = FakeLLMProvider(responses=[MALFORMED_DIFF])
        session = MagicMock()

        after_rev = MagicMock()
        after_rev.local_path = str(tmp_path)

        with (
            patch("kmp_repair_pipeline.repair.repairer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.repair.repairer.to_db"),
            patch("kmp_repair_pipeline.repair.repairer.RevisionRepo") as MockRevRepo,
            patch("kmp_repair_pipeline.repair.repairer.PatchAttemptRepo") as MockPatchRepo,
            patch("kmp_repair_pipeline.repair.repairer.AgentLogRepo"),
            patch("kmp_repair_pipeline.repair.repairer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.repair.repairer.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.repair.repairer.apply_patch") as mock_apply,
        ):
            MockRevRepo.return_value.get.return_value = after_rev
            MockPatchRepo.return_value.list_for_case.return_value = []
            attempt_row = MagicMock()
            MockPatchRepo.return_value.create.return_value = attempt_row
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()
            store = MagicMock()
            store.write_prompt.return_value = ("/p/prompt.txt", "sha1")
            store.write_response.return_value = ("/p/response.txt", "sha2")
            store.write_patch.return_value = ("/p/patch.diff", "sha3")
            MockStore.return_value = store

            result = repair(
                case_id="case-001",
                session=session,
                artifact_base=tmp_path / "artifacts",
                repair_mode="full_thesis",
                provider=provider,
            )

        assert result.patch_status == "FAILED_APPLY"
        assert "patch_strategy=single_diff" in attempt_row.retry_reason
        assert "malformed diff precheck failed" in attempt_row.retry_reason
        mock_apply.assert_not_called()

    def test_force_patch_attempt_retries_after_impossible(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import repair

        bundle = self._make_bundle()
        provider = FakeLLMProvider(responses=["PATCH_IMPOSSIBLE", SAMPLE_DIFF])
        session = MagicMock()

        after_rev = MagicMock()
        after_rev.local_path = str(tmp_path)

        with (
            patch("kmp_repair_pipeline.repair.repairer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.repair.repairer.to_db"),
            patch("kmp_repair_pipeline.repair.repairer.RevisionRepo") as MockRevRepo,
            patch("kmp_repair_pipeline.repair.repairer.PatchAttemptRepo") as MockPatchRepo,
            patch("kmp_repair_pipeline.repair.repairer.AgentLogRepo"),
            patch("kmp_repair_pipeline.repair.repairer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.repair.repairer.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.repair.repairer.apply_patch") as mock_apply,
        ):
            MockRevRepo.return_value.get.return_value = after_rev
            MockPatchRepo.return_value.list_for_case.return_value = []
            attempt_row = MagicMock()
            MockPatchRepo.return_value.create.return_value = attempt_row
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()
            store = MagicMock()
            store.write_prompt.return_value = ("/p/prompt.txt", "sha1")
            store.write_response.return_value = ("/p/response.txt", "sha2")
            store.write_patch.return_value = ("/p/patch.diff", "sha3")
            MockStore.return_value = store
            mock_apply.return_value = PatchApplicationResult(
                success=True, touched_files=["src/commonMain/kotlin/App.kt"]
            )

            result = repair(
                case_id="case-001",
                session=session,
                artifact_base=tmp_path / "artifacts",
                repair_mode="full_thesis",
                provider=provider,
                force_patch_attempt=True,
            )

        assert result.patch_status == "APPLIED"
        assert len(provider.calls) == 2
        assert "forced patch retry used" in attempt_row.retry_reason
        assert store.write_prompt.call_count == 2


class TestPatchStrategies:
    def test_split_diff_by_file_returns_two_blocks(self) -> None:
        from kmp_repair_pipeline.repair.repairer import _split_diff_by_file

        blocks = _split_diff_by_file(SAMPLE_DIFF)
        assert len(blocks) == 2
        assert "+++ b/src/commonMain/kotlin/App.kt" in blocks[0]
        assert "+++ b/src/androidMain/kotlin/Platform.kt" in blocks[1]

    def test_chain_by_file_stops_on_first_failure(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _apply_patch_chain_by_file

        calls = {"count": 0}

        def fake_apply(_diff_text, _repo_path):
            calls["count"] += 1
            if calls["count"] == 1:
                return PatchApplicationResult(success=True, touched_files=["src/commonMain/kotlin/App.kt"])
            return PatchApplicationResult(success=False, stderr="hunk failed")

        with patch("kmp_repair_pipeline.repair.repairer.apply_patch", side_effect=fake_apply):
            result = _apply_patch_chain_by_file(SAMPLE_DIFF, tmp_path)

        assert calls["count"] == 2
        assert result.success is False
        assert "block 2/2" in result.stderr
        assert "src/commonMain/kotlin/App.kt" in result.touched_files

    def test_chain_by_file_all_success(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.repair.repairer import _apply_patch_chain_by_file

        with patch(
            "kmp_repair_pipeline.repair.repairer.apply_patch",
            return_value=PatchApplicationResult(success=True, touched_files=["some/file.kt"]),
        ) as mock_apply:
            result = _apply_patch_chain_by_file(SAMPLE_DIFF, tmp_path)

        assert mock_apply.call_count == 2
        assert result.success is True
        assert "some/file.kt" in result.touched_files


class TestDiffPrecheck:
    def test_valid_diff_passes(self) -> None:
        from kmp_repair_pipeline.repair.repairer import _precheck_unified_diff

        ok, detail = _precheck_unified_diff(SAMPLE_DIFF)
        assert ok is True
        assert detail == ""

    def test_malformed_diff_fails(self) -> None:
        from kmp_repair_pipeline.repair.repairer import _precheck_unified_diff

        ok, detail = _precheck_unified_diff(MALFORMED_DIFF)
        assert ok is False
        assert "invalid hunk line" in detail

    def test_markdown_fenced_diff_is_normalized(self) -> None:
        from kmp_repair_pipeline.repair.repairer import (
            _normalize_model_diff_output,
            _precheck_unified_diff,
        )

        fenced = f"```diff\n{SAMPLE_DIFF}```"
        normalized = _normalize_model_diff_output(fenced)
        assert normalized.startswith("--- a/")
        assert "```" not in normalized
        ok, detail = _precheck_unified_diff(normalized)
        assert ok is True
        assert detail == ""


# ---------------------------------------------------------------------------
# baselines — run_baseline
# ---------------------------------------------------------------------------


class TestRunBaseline:
    def test_iterative_stops_on_success(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.baselines.baseline_runner import run_baseline

        call_count = 0

        def fake_repair(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.patch_status = "APPLIED" if call_count == 2 else "FAILED_APPLY"
            r.attempt_number = call_count
            return r

        with patch("kmp_repair_pipeline.baselines.baseline_runner.repair", side_effect=fake_repair):
            session = MagicMock()
            result = run_baseline("case-1", session, mode="iterative_agentic")

        assert result.applied is True
        assert call_count == 2  # stopped after first success

    def test_run_baseline_invalid_mode_raises(self) -> None:
        from kmp_repair_pipeline.baselines.baseline_runner import run_baseline
        with pytest.raises(ValueError, match="Unknown baseline mode"):
            run_baseline("case-1", MagicMock(), mode="invalid_mode")
