"""Unit tests for Phase 11 — explanation_agent, explainer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.case_bundle.evidence import ExplanationEvidence
from kmp_repair_pipeline.explanation.explanation_agent import (
    AGENT_TYPE,
    _build_prompt,
    _deterministic_fallback,
    _parse_response,
    render_markdown,
    run_explanation_agent,
)
from kmp_repair_pipeline.utils.llm_provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


def _sample_ctx() -> dict:
    return {
        "update": {
            "update_class": "direct_library",
            "version_changes": [
                {"dependency_group": "io.ktor", "before": "3.1.3", "after": "3.4.1"}
            ],
        },
        "execution_summary": {
            "before_status": "SUCCESS_REPOSITORY_LEVEL",
            "after_status": "FAILED_BUILD",
            "error_count": 3,
        },
        "localization": {
            "candidates": [
                {"rank": 1, "file_path": "src/commonMain/kotlin/App.kt",
                 "source_set": "common", "score": 0.9},
            ]
        },
        "patch": {"status": "VALIDATED", "diff_path": "data/artifacts/case-001/patches/001_full_thesis.diff"},
        "validation": {
            "repository_level_status": "SUCCESS_REPOSITORY_LEVEL",
            "target_results": [
                {"target": "shared", "status": "SUCCESS_REPOSITORY_LEVEL"},
            ],
        },
    }


class TestBuildPrompt:
    def test_contains_dep_group(self) -> None:
        p = _build_prompt(_sample_ctx())
        assert "io.ktor" in p

    def test_contains_version_range(self) -> None:
        p = _build_prompt(_sample_ctx())
        assert "3.1.3" in p
        assert "3.4.1" in p

    def test_contains_candidate_file(self) -> None:
        p = _build_prompt(_sample_ctx())
        assert "App.kt" in p

    def test_contains_validation_status(self) -> None:
        p = _build_prompt(_sample_ctx())
        assert "SUCCESS_REPOSITORY_LEVEL" in p

    def test_empty_ctx_does_not_crash(self) -> None:
        p = _build_prompt({})
        assert isinstance(p, str)


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def _valid_json(self) -> str:
        return json.dumps({
            "what_was_updated": "io.ktor updated 3.1.3→3.4.1",
            "update_class_rationale": "Direct library dep",
            "localization_summary": "App.kt directly imports ktor",
            "patch_rationale": "Replaced HttpClient import",
            "validation_summary": "Build succeeded on shared target",
            "target_coverage_complete": True,
            "uncertainties": [],
        })

    def test_valid_json_parses_correctly(self) -> None:
        ev = _parse_response(self._valid_json(), {})
        assert ev.what_was_updated == "io.ktor updated 3.1.3→3.4.1"
        assert ev.target_coverage_complete is True
        assert ev.uncertainties == []

    def test_uncertainty_parsed(self) -> None:
        data = json.loads(self._valid_json())
        data["uncertainties"] = [{"kind": "environment", "description": "iOS not available"}]
        data["target_coverage_complete"] = False
        ev = _parse_response(json.dumps(data), {})
        assert len(ev.uncertainties) == 1
        assert ev.uncertainties[0].kind == "environment"

    def test_invalid_json_falls_back(self) -> None:
        ev = _parse_response("not json at all", _sample_ctx())
        assert isinstance(ev, ExplanationEvidence)
        assert "io.ktor" in ev.what_was_updated

    def test_markdown_fences_stripped(self) -> None:
        fenced = "```json\n" + self._valid_json() + "\n```"
        ev = _parse_response(fenced, {})
        assert ev.what_was_updated == "io.ktor updated 3.1.3→3.4.1"

    def test_empty_response_falls_back(self) -> None:
        ev = _parse_response("", _sample_ctx())
        assert isinstance(ev, ExplanationEvidence)


# ---------------------------------------------------------------------------
# _deterministic_fallback
# ---------------------------------------------------------------------------


class TestDeterministicFallback:
    def test_mentions_dep_group(self) -> None:
        ev = _deterministic_fallback(_sample_ctx())
        assert "io.ktor" in ev.what_was_updated

    def test_unavailable_target_adds_uncertainty(self) -> None:
        ctx = _sample_ctx()
        ctx["validation"]["target_results"].append(
            {"target": "ios", "status": "NOT_RUN_ENVIRONMENT_UNAVAILABLE"}
        )
        ev = _deterministic_fallback(ctx)
        assert ev.target_coverage_complete is False
        assert any(u.kind == "environment" for u in ev.uncertainties)

    def test_all_runnable_no_uncertainty(self) -> None:
        ev = _deterministic_fallback(_sample_ctx())
        env_uncertainties = [u for u in ev.uncertainties if u.kind == "environment"]
        assert env_uncertainties == []


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def _sample_evidence(self) -> ExplanationEvidence:
        return ExplanationEvidence(
            what_was_updated="io.ktor 3.1.3→3.4.1",
            update_class_rationale="Direct library",
            localization_summary="App.kt",
            patch_rationale="Wildcard import",
            validation_summary="Build passed",
            target_coverage_complete=True,
            uncertainties=[],
            model_id="claude-sonnet-4-6",
            tokens_in=100,
            tokens_out=200,
        )

    def test_contains_case_id(self) -> None:
        md = render_markdown(self._sample_evidence(), "case-abc-001")
        assert "case-abc" in md

    def test_contains_what_was_updated(self) -> None:
        md = render_markdown(self._sample_evidence(), "case-abc")
        assert "io.ktor" in md

    def test_uncertainties_section_absent_when_empty(self) -> None:
        md = render_markdown(self._sample_evidence(), "c")
        assert "Uncertainties" not in md

    def test_uncertainties_section_present(self) -> None:
        ev = self._sample_evidence()
        ev.uncertainties = [ExplanationEvidence.Uncertainty(
            kind="environment", description="iOS not available"
        )]
        md = render_markdown(ev, "c")
        assert "Uncertainties" in md
        assert "iOS not available" in md

    def test_model_provenance_shown(self) -> None:
        md = render_markdown(self._sample_evidence(), "c")
        assert "claude-sonnet-4-6" in md


# ---------------------------------------------------------------------------
# run_explanation_agent — FakeLLMProvider
# ---------------------------------------------------------------------------


class TestRunExplanationAgent:
    def _fake_response(self) -> str:
        return json.dumps({
            "what_was_updated": "io.ktor 3.1.3→3.4.1",
            "update_class_rationale": "Direct library",
            "localization_summary": "App.kt needs fix",
            "patch_rationale": "Wildcard import fixes it",
            "validation_summary": "Build passed on shared",
            "target_coverage_complete": True,
            "uncertainties": [],
        })

    def test_returns_agent_output(self) -> None:
        provider = FakeLLMProvider(responses=[self._fake_response()])
        out = run_explanation_agent(_sample_ctx(), provider)
        assert out.evidence.what_was_updated == "io.ktor 3.1.3→3.4.1"
        assert out.evidence.model_id == "fake-model-1.0"
        assert out.evidence.tokens_in is not None

    def test_prompt_contains_dep(self) -> None:
        provider = FakeLLMProvider(responses=[self._fake_response()])
        out = run_explanation_agent(_sample_ctx(), provider)
        assert "io.ktor" in out.prompt

    def test_invalid_llm_response_falls_back(self) -> None:
        provider = FakeLLMProvider(responses=["{{invalid}}"])
        out = run_explanation_agent(_sample_ctx(), provider)
        # Fallback should still produce valid evidence
        assert isinstance(out.evidence, ExplanationEvidence)


# ---------------------------------------------------------------------------
# explain() — orchestrator (patched DB)
# ---------------------------------------------------------------------------


def _make_bundle():
    from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
    from kmp_repair_pipeline.case_bundle.evidence import (
        ExecutionEvidence, LocalizationResult, RepairEvidence,
        RevisionExecution, SourceSetMap, StructuralEvidence, UpdateEvidence,
        ValidationEvidence,
    )
    from kmp_repair_pipeline.domain.events import (
        DependencyUpdateEvent, UpdateClass, VersionChange,
    )
    from kmp_repair_pipeline.domain.validation import ValidationStatus

    bundle = CaseBundle(
        meta=CaseMeta(
            case_id="case-011",
            event_id="ev-11",
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
    bundle.validation = ValidationEvidence(
        target_results=[],
        repository_level_status=ValidationStatus.SUCCESS_REPOSITORY_LEVEL,
    )
    return bundle


class TestExplain:
    def _fake_json(self) -> str:
        return json.dumps({
            "what_was_updated": "io.ktor 3.1.3→3.4.1",
            "update_class_rationale": "Direct library",
            "localization_summary": "App.kt",
            "patch_rationale": "Wildcard import",
            "validation_summary": "Passed",
            "target_coverage_complete": True,
            "uncertainties": [],
        })

    def test_explain_writes_artifacts_and_advances_status(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.explanation.explainer import explain

        bundle = _make_bundle()
        provider = FakeLLMProvider(responses=[self._fake_json()])
        session = MagicMock()

        store = MagicMock()
        store.write_prompt.return_value = ("/p/prompt.txt", "sha1")
        store.write_response.return_value = ("/p/resp.txt", "sha2")
        store.write_explanation_json.return_value = ("/p/exp.json", "sha3")
        store.write_explanation_markdown.return_value = ("/p/exp.md", "sha4")

        with (
            patch("kmp_repair_pipeline.explanation.explainer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.explanation.explainer.to_db"),
            patch("kmp_repair_pipeline.explanation.explainer.ArtifactStore") as MockStore,
            patch("kmp_repair_pipeline.explanation.explainer.AgentLogRepo") as MockLog,
            patch("kmp_repair_pipeline.explanation.explainer.ExplanationRepo") as MockExp,
            patch("kmp_repair_pipeline.explanation.explainer.PatchAttemptRepo") as MockPatch,
            patch("kmp_repair_pipeline.explanation.explainer.RepairCaseRepo") as MockCase,
        ):
            MockStore.return_value = store
            MockLog.return_value.list_for_case.return_value = []
            MockLog.return_value.create.return_value = MagicMock()
            MockExp.return_value.create.return_value = MagicMock()
            MockPatch.return_value.list_for_case.return_value = []
            MockCase.return_value.get_by_id.return_value = MagicMock()

            result = explain(
                case_id="case-011",
                session=session,
                artifact_base=tmp_path / "artifacts",
                provider=provider,
            )

        assert result.json_path == "/p/exp.json"
        assert result.markdown_path == "/p/exp.md"
        assert bundle.meta.status == "EXPLAINED"
        assert bundle.explanation is not None
        assert bundle.explanation.what_was_updated == "io.ktor 3.1.3→3.4.1"

    def test_explain_case_not_found_raises(self) -> None:
        from kmp_repair_pipeline.explanation.explainer import explain

        with patch("kmp_repair_pipeline.explanation.explainer.from_db_case", return_value=None):
            with pytest.raises(ValueError, match="not found"):
                explain(case_id="bad-id", session=MagicMock())
