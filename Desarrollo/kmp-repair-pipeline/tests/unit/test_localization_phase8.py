"""Unit tests for Phase 8 — scoring, localization_agent, llm_provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.case_bundle.evidence import ErrorObservation, StructuralEvidence
from kmp_repair_pipeline.case_bundle.evidence import SourceSetMap
from kmp_repair_pipeline.domain.analysis import (
    ExpectActualPair,
    FileImpact,
    ImpactGraph,
    ImpactRelation,
)
from kmp_repair_pipeline.localization.scoring import (
    _classify,
    _compute_dynamic_score,
    _compute_static_score,
    _count_error_mentions,
    score_candidates,
)
from kmp_repair_pipeline.utils.llm_provider import FakeLLMProvider, NoOpProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_impact(
    path: str,
    relation: ImpactRelation = ImpactRelation.DIRECT,
    distance: int = 0,
    source_set: str = "common",
) -> FileImpact:
    return FileImpact(
        file_path=path,
        relation=relation,
        distance=distance,
        source_set=source_set,
    )


def _make_graph(*paths_relations: tuple[str, ImpactRelation, int]) -> ImpactGraph:
    impacted = [_make_impact(p, r, d) for p, r, d in paths_relations]
    return ImpactGraph(
        dependency_group="io.ktor",
        version_before="3.1.3",
        version_after="3.4.1",
        seed_files=[p for p, r, d in paths_relations if r == ImpactRelation.DIRECT],
        impacted_files=impacted,
        total_project_files=20,
        total_impacted=len(impacted),
    )


def _make_structural(common: list[str] = (), android: list[str] = ()) -> StructuralEvidence:
    return StructuralEvidence(
        source_set_map=SourceSetMap(
            common_files=list(common),
            android_files=list(android),
        ),
        total_kotlin_files=len(common) + len(android),
    )


def _err(file_path: str, line: int = 1) -> ErrorObservation:
    return ErrorObservation(
        error_type="COMPILE_ERROR",
        file_path=file_path,
        line=line,
        message="Unresolved reference",
    )


# ---------------------------------------------------------------------------
# scoring — _count_error_mentions
# ---------------------------------------------------------------------------


class TestCountErrorMentions:
    def test_counts_by_basename(self) -> None:
        errors = [
            _err("src/commonMain/kotlin/App.kt"),
            _err("src/commonMain/kotlin/App.kt"),
            _err("src/androidMain/kotlin/Other.kt"),
        ]
        counts = _count_error_mentions(errors)
        assert counts["App.kt"] == 2
        assert counts["Other.kt"] == 1

    def test_no_file_path_ignored(self) -> None:
        errors = [ErrorObservation(error_type="COMPILE_ERROR", message="bad")]
        counts = _count_error_mentions(errors)
        assert counts == {}


# ---------------------------------------------------------------------------
# scoring — _compute_static_score
# ---------------------------------------------------------------------------


class TestComputeStaticScore:
    def test_direct_import_gets_highest_score(self) -> None:
        fi = _make_impact("App.kt", ImpactRelation.DIRECT, 0)
        score = _compute_static_score(fi, {"App.kt"}, set())
        assert score >= 1.0  # capped at 1.0

    def test_transitive_lower_than_direct(self) -> None:
        direct = _make_impact("A.kt", ImpactRelation.DIRECT, 0)
        transitive = _make_impact("B.kt", ImpactRelation.TRANSITIVE, 1)
        s_direct = _compute_static_score(direct, set(), set())
        s_transitive = _compute_static_score(transitive, set(), set())
        assert s_direct > s_transitive

    def test_distance_decay_applied(self) -> None:
        near = _make_impact("A.kt", ImpactRelation.TRANSITIVE, 1)
        far = _make_impact("B.kt", ImpactRelation.TRANSITIVE, 3)
        s_near = _compute_static_score(near, set(), set())
        s_far = _compute_static_score(far, set(), set())
        assert s_near > s_far

    def test_expect_actual_bonus(self) -> None:
        fi = _make_impact("App.kt", ImpactRelation.TRANSITIVE, 1)
        without = _compute_static_score(fi, set(), set())
        with_bonus = _compute_static_score(fi, set(), {"App.kt"})
        assert with_bonus > without

    def test_score_capped_at_one(self) -> None:
        fi = _make_impact("App.kt", ImpactRelation.DIRECT, 0)
        score = _compute_static_score(fi, {"App.kt"}, {"App.kt"})
        assert score <= 1.0


# ---------------------------------------------------------------------------
# scoring — _compute_dynamic_score
# ---------------------------------------------------------------------------


class TestComputeDynamicScore:
    def test_error_mention_boosts_score(self) -> None:
        counts = {"App.kt": 3}
        score = _compute_dynamic_score("src/commonMain/kotlin/App.kt", counts)
        assert score > 0.0

    def test_no_mention_zero_score(self) -> None:
        score = _compute_dynamic_score("App.kt", {})
        assert score == 0.0

    def test_score_capped_at_one(self) -> None:
        # Even 100 mentions should not exceed 1.0
        counts = {"App.kt": 100}
        score = _compute_dynamic_score("App.kt", counts)
        assert score <= 1.0


# ---------------------------------------------------------------------------
# scoring — score_candidates
# ---------------------------------------------------------------------------


class TestScoreCandidates:
    def test_direct_import_ranks_first(self) -> None:
        graph = _make_graph(
            ("common/App.kt", ImpactRelation.DIRECT, 0),
            ("common/Utils.kt", ImpactRelation.TRANSITIVE, 2),
        )
        ranked = score_candidates(graph, None, [])
        assert ranked[0].file_path == "common/App.kt"

    def test_error_observations_boost_score(self) -> None:
        graph = _make_graph(
            ("common/App.kt", ImpactRelation.TRANSITIVE, 2),
            ("common/Other.kt", ImpactRelation.TRANSITIVE, 2),
        )
        errors = [_err("common/App.kt"), _err("common/App.kt")]
        ranked = score_candidates(graph, None, errors)
        # App.kt should outrank Other.kt due to error mentions
        app_cand = next(c for c in ranked if c.file_path == "common/App.kt")
        other_cand = next(c for c in ranked if c.file_path == "common/Other.kt")
        assert app_cand.final_score > other_cand.final_score

    def test_source_set_assigned_from_structural(self) -> None:
        graph = _make_graph(("src/commonMain/kotlin/App.kt", ImpactRelation.DIRECT, 0))
        structural = _make_structural(common=["src/commonMain/kotlin/App.kt"])
        ranked = score_candidates(graph, structural, [])
        assert ranked[0].source_set == "common"

    def test_none_graph_falls_back_to_errors_only(self) -> None:
        errors = [_err("src/commonMain/kotlin/Broken.kt")]
        ranked = score_candidates(None, None, errors)
        assert len(ranked) == 1
        assert ranked[0].file_path == "src/commonMain/kotlin/Broken.kt"

    def test_empty_inputs_returns_empty(self) -> None:
        graph = _make_graph()
        ranked = score_candidates(graph, None, [])
        assert ranked == []

    def test_score_breakdown_contains_relation(self) -> None:
        graph = _make_graph(("A.kt", ImpactRelation.DIRECT, 0))
        ranked = score_candidates(graph, None, [])
        assert "relation" in ranked[0].score_breakdown
        assert ranked[0].score_breakdown["relation"] == "direct"


# ---------------------------------------------------------------------------
# localization_agent — _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_contains_dep_group(self) -> None:
        from kmp_repair_pipeline.localization.localization_agent import _build_prompt
        from kmp_repair_pipeline.localization.scoring import ScoredCandidate

        context = {
            "update": {
                "version_changes": [{"dependency_group": "io.ktor", "before": "3.1", "after": "3.4"}],
                "update_class": "direct_library",
            },
            "execution_errors": [],
            "structural": {"direct_import_files": [], "expect_actual_pairs": [], "relevant_build_files": []},
        }
        prompt = _build_prompt(context, [])
        assert "io.ktor" in prompt
        assert "3.1" in prompt
        assert "3.4" in prompt

    def test_prompt_includes_error_lines(self) -> None:
        from kmp_repair_pipeline.localization.localization_agent import _build_prompt
        context = {
            "update": {"version_changes": [], "update_class": "?"},
            "execution_errors": [{"error_type": "COMPILE_ERROR", "file_path": "App.kt", "line": 42, "message": "Unresolved"}],
            "structural": {"direct_import_files": [], "expect_actual_pairs": [], "relevant_build_files": []},
        }
        prompt = _build_prompt(context, [])
        assert "App.kt" in prompt
        assert "42" in prompt


# ---------------------------------------------------------------------------
# localization_agent — _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def _valid_response(self) -> str:
        return json.dumps({
            "candidates": [
                {"rank": 1, "file_path": "A.kt", "source_set": "common",
                 "classification": "shared_code", "score": 0.9, "rationale": "direct import"},
                {"rank": 2, "file_path": "B.kt", "source_set": "android",
                 "classification": "platform_specific", "score": 0.6, "rationale": "transitive"},
            ],
            "agent_notes": "A.kt is most likely affected"
        })

    def test_parses_valid_json(self) -> None:
        from kmp_repair_pipeline.localization.localization_agent import _parse_response
        candidates = _parse_response(self._valid_response(), [])
        assert len(candidates) == 2
        assert candidates[0].file_path == "A.kt"
        assert candidates[0].rank == 1
        assert candidates[1].source_set == "android"

    def test_falls_back_on_invalid_json(self) -> None:
        from kmp_repair_pipeline.localization.localization_agent import (
            _parse_response,
            _deterministic_to_result_candidates,
        )
        from kmp_repair_pipeline.localization.scoring import ScoredCandidate

        fallback = [ScoredCandidate("X.kt", "common", 0.8, 0.0, 0.8, "shared_code")]
        candidates = _parse_response("THIS IS NOT JSON", fallback)
        assert candidates[0].file_path == "X.kt"

    def test_strips_markdown_fences(self) -> None:
        from kmp_repair_pipeline.localization.localization_agent import _parse_response
        wrapped = f"```json\n{self._valid_response()}\n```"
        candidates = _parse_response(wrapped, [])
        assert len(candidates) == 2


# ---------------------------------------------------------------------------
# FakeLLMProvider
# ---------------------------------------------------------------------------


class TestFakeLLMProvider:
    def test_returns_programmed_responses_in_order(self) -> None:
        provider = FakeLLMProvider(responses=["first", "second"])
        r1 = provider.complete("p1")
        r2 = provider.complete("p2")
        assert r1.content == "first"
        assert r2.content == "second"

    def test_uses_default_when_exhausted(self) -> None:
        provider = FakeLLMProvider(responses=[], default="default-resp")
        r = provider.complete("p")
        assert r.content == "default-resp"

    def test_records_calls(self) -> None:
        provider = FakeLLMProvider()
        provider.complete("hello", system="sys")
        assert len(provider.calls) == 1
        assert provider.calls[0]["prompt"] == "hello"

    def test_noop_provider_raises(self) -> None:
        provider = NoOpProvider()
        with pytest.raises(AssertionError, match="unexpectedly"):
            provider.complete("p")


# ---------------------------------------------------------------------------
# localizer — localize (patched)
# ---------------------------------------------------------------------------


class TestLocalize:
    def _make_bundle(self, case_id: str = "case-001", include_impact_graph: bool = True):
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
        from kmp_repair_pipeline.case_bundle.evidence import (
            ExecutionEvidence, RevisionExecution, UpdateEvidence,
        )
        from kmp_repair_pipeline.domain.events import (
            DependencyUpdateEvent, UpdateClass, VersionChange,
        )
        from kmp_repair_pipeline.domain.validation import ValidationStatus

        bundle = CaseBundle(
            meta=CaseMeta(
                case_id=case_id, event_id="ev-1",
                repository_url="https://github.com/test/repo",
                status="ANALYZED",
            )
        )
        bundle.update_evidence = UpdateEvidence(
            update_event=DependencyUpdateEvent(repo_url="https://github.com/test/repo"),
            version_changes=[VersionChange(dependency_group="io.ktor", version_key="ktor",
                                           before="3.1.3", after="3.4.1")],
            update_class=UpdateClass.DIRECT_LIBRARY,
        )
        bundle.structural = StructuralEvidence(
            source_set_map=SourceSetMap(
                common_files=["src/commonMain/kotlin/App.kt"],
            ),
            direct_import_files=["src/commonMain/kotlin/App.kt"],
            total_kotlin_files=5,
            impact_graph=(
                _make_graph(
                    ("src/commonMain/kotlin/App.kt", ImpactRelation.DIRECT, 0),
                )
                if include_impact_graph else None
            ),
        )
        bundle.execution = ExecutionEvidence(
            after=RevisionExecution(
                revision_type="after",
                overall_status=ValidationStatus.FAILED_BUILD,
                error_observations=[_err("src/commonMain/kotlin/App.kt")],
            )
        )
        return bundle

    def test_localize_no_agent_sets_localized_status(self) -> None:
        from kmp_repair_pipeline.localization.localizer import localize

        bundle = self._make_bundle()
        session = MagicMock()

        with (
            patch("kmp_repair_pipeline.localization.localizer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.localization.localizer.to_db"),
            patch("kmp_repair_pipeline.localization.localizer.LocalizationCandidateRepo"),
            patch("kmp_repair_pipeline.localization.localizer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.localization.localizer.AgentLogRepo"),
        ):
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()

            result = localize(
                case_id="case-001",
                session=session,
                use_agent=False,
            )

        assert bundle.meta.status == "LOCALIZED"
        assert result.used_agent is False
        assert result.total_candidates >= 1

    def test_localize_with_fake_agent(self) -> None:
        from kmp_repair_pipeline.localization.localizer import localize

        bundle = self._make_bundle()
        session = MagicMock()

        fake_response = json.dumps({
            "candidates": [
                {"rank": 1, "file_path": "src/commonMain/kotlin/App.kt",
                 "source_set": "common", "classification": "shared_code",
                 "score": 0.95, "rationale": "Direct import"},
            ],
            "agent_notes": "App.kt is the culprit",
        })
        provider = FakeLLMProvider(responses=[fake_response])

        with (
            patch("kmp_repair_pipeline.localization.localizer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.localization.localizer.to_db"),
            patch("kmp_repair_pipeline.localization.localizer.LocalizationCandidateRepo"),
            patch("kmp_repair_pipeline.localization.localizer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.localization.localizer.AgentLogRepo"),
            patch("kmp_repair_pipeline.localization.localizer.ArtifactStore"),
            patch("kmp_repair_pipeline.localization.localizer._next_agent_call_index", return_value=0),
        ):
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()

            result = localize(
                case_id="case-001",
                session=session,
                use_agent=True,
                provider=provider,
            )

        assert result.used_agent is True
        assert result.agent_notes == "App.kt is the culprit"
        assert bundle.meta.status == "LOCALIZED"
        assert len(provider.calls) == 1

    def test_localize_rebuilds_graph_when_not_serialized(self) -> None:
        from kmp_repair_pipeline.localization.localizer import localize

        bundle = self._make_bundle(include_impact_graph=False)
        session = MagicMock()
        rebuilt_graph = _make_graph(
            ("src/commonMain/kotlin/App.kt", ImpactRelation.DIRECT, 0),
        )

        with (
            patch("kmp_repair_pipeline.localization.localizer.from_db_case", return_value=bundle),
            patch("kmp_repair_pipeline.localization.localizer.to_db"),
            patch("kmp_repair_pipeline.localization.localizer.LocalizationCandidateRepo"),
            patch("kmp_repair_pipeline.localization.localizer.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.localization.localizer.AgentLogRepo"),
            patch("kmp_repair_pipeline.localization.localizer.RevisionRepo") as MockRevisionRepo,
            patch("kmp_repair_pipeline.localization.localizer.run_static_analysis", return_value=rebuilt_graph) as mock_run_static,
        ):
            MockCaseRepo.return_value.get_by_id.return_value = MagicMock()
            MockRevisionRepo.return_value.get.return_value = MagicMock(local_path="/tmp/repo")

            result = localize(
                case_id="case-001",
                session=session,
                use_agent=False,
            )

        assert result.total_candidates >= 1
        assert mock_run_static.call_count == 1
