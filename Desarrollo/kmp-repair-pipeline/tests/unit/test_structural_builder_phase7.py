"""Unit tests for Phase 7 — structural_builder.py."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.domain.analysis import (
    ExpectActualPair,
    FileImpact,
    FileParseResult,
    ImpactGraph,
    ImpactRelation,
)
from kmp_repair_pipeline.static_analysis.structural_builder import (
    _build_source_set_map,
    _find_build_files,
    _merge_graphs,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic parse results
# ---------------------------------------------------------------------------


def _make_parse_result(
    file_path: str,
    source_set: str = "common",
    package: str = "com.example",
    imports: list[str] | None = None,
) -> FileParseResult:
    return FileParseResult(
        file_path=file_path,
        source_set=source_set,
        package=package,
        imports=imports or [],
    )


def _make_impact_graph(
    dep: str = "io.ktor",
    seeds: list[str] | None = None,
    impacted: list[str] | None = None,
    pairs: list[ExpectActualPair] | None = None,
    total_files: int = 10,
) -> ImpactGraph:
    seed_list = seeds or ["src/commonMain/kotlin/App.kt"]
    impacted_list = [
        FileImpact(file_path=f, relation=ImpactRelation.DIRECT, source_set="common")
        for f in (impacted or seed_list)
    ]
    return ImpactGraph(
        dependency_group=dep,
        version_before="3.1.3",
        version_after="3.4.1",
        seed_files=seed_list,
        impacted_files=impacted_list,
        expect_actual_pairs=pairs or [],
        total_project_files=total_files,
        total_impacted=len(impacted_list),
    )


# ---------------------------------------------------------------------------
# _build_source_set_map
# ---------------------------------------------------------------------------


class TestBuildSourceSetMap:
    def test_common_files_grouped(self) -> None:
        prs = [
            _make_parse_result("src/commonMain/kotlin/A.kt", "common"),
            _make_parse_result("src/commonMain/kotlin/B.kt", "common"),
        ]
        ssm = _build_source_set_map(prs)
        assert len(ssm.common_files) == 2
        assert "src/commonMain/kotlin/A.kt" in ssm.common_files

    def test_android_and_ios_split(self) -> None:
        prs = [
            _make_parse_result("src/androidMain/kotlin/Android.kt", "android"),
            _make_parse_result("src/iosMain/kotlin/iOS.kt", "ios"),
        ]
        ssm = _build_source_set_map(prs)
        assert ssm.android_files == ["src/androidMain/kotlin/Android.kt"]
        assert ssm.ios_files == ["src/iosMain/kotlin/iOS.kt"]
        assert ssm.common_files == []

    def test_unknown_source_set_goes_to_other(self) -> None:
        prs = [_make_parse_result("src/wasmMain/kotlin/Wasm.kt", "wasm")]
        ssm = _build_source_set_map(prs)
        assert "wasm" in ssm.other
        assert ssm.other["wasm"] == ["src/wasmMain/kotlin/Wasm.kt"]

    def test_source_set_for_lookup(self) -> None:
        prs = [
            _make_parse_result("src/commonMain/kotlin/A.kt", "common"),
            _make_parse_result("src/androidMain/kotlin/B.kt", "android"),
        ]
        ssm = _build_source_set_map(prs)
        assert ssm.source_set_for("src/commonMain/kotlin/A.kt") == "common"
        assert ssm.source_set_for("src/androidMain/kotlin/B.kt") == "android"
        assert ssm.source_set_for("nonexistent.kt") == "unknown"


# ---------------------------------------------------------------------------
# _merge_graphs
# ---------------------------------------------------------------------------


class TestMergeGraphs:
    def test_single_graph_returned_as_is(self) -> None:
        g = _make_impact_graph("io.ktor")
        merged = _merge_graphs([g])
        assert merged is g

    def test_empty_list_returns_none(self) -> None:
        assert _merge_graphs([]) is None

    def test_union_of_impacted_files(self) -> None:
        g1 = _make_impact_graph("io.ktor", seeds=["A.kt"], impacted=["A.kt", "B.kt"])
        g2 = _make_impact_graph("io.koin", seeds=["C.kt"], impacted=["C.kt", "B.kt"])
        merged = _merge_graphs([g1, g2])
        paths = {fi.file_path for fi in merged.impacted_files}
        assert "A.kt" in paths
        assert "B.kt" in paths
        assert "C.kt" in paths
        # B.kt appears in both but should not be duplicated
        assert len([fi for fi in merged.impacted_files if fi.file_path == "B.kt"]) == 1

    def test_union_of_seeds(self) -> None:
        g1 = _make_impact_graph("io.ktor", seeds=["A.kt"])
        g2 = _make_impact_graph("io.koin", seeds=["C.kt"])
        merged = _merge_graphs([g1, g2])
        assert "A.kt" in merged.seed_files
        assert "C.kt" in merged.seed_files

    def test_expect_actual_pairs_deduplicated(self) -> None:
        pair = ExpectActualPair(
            expect_fqcn="com.App",
            expect_file="common/App.kt",
            actual_files=["android/App.kt"],
        )
        g1 = _make_impact_graph("io.ktor", pairs=[pair])
        g2 = _make_impact_graph("io.koin", pairs=[pair])  # same pair
        merged = _merge_graphs([g1, g2])
        assert len(merged.expect_actual_pairs) == 1

    def test_total_impacted_updated(self) -> None:
        g1 = _make_impact_graph("io.ktor", impacted=["A.kt", "B.kt"])
        g2 = _make_impact_graph("io.koin", impacted=["C.kt"])
        merged = _merge_graphs([g1, g2])
        assert merged.total_impacted == 3

    def test_dependency_group_is_comma_joined(self) -> None:
        g1 = _make_impact_graph("io.ktor")
        g2 = _make_impact_graph("io.koin")
        merged = _merge_graphs([g1, g2])
        assert "io.ktor" in merged.dependency_group
        assert "io.koin" in merged.dependency_group


# ---------------------------------------------------------------------------
# _find_build_files
# ---------------------------------------------------------------------------


class TestFindBuildFiles:
    def test_finds_version_catalog(self, tmp_path: Path) -> None:
        (tmp_path / "gradle").mkdir()
        (tmp_path / "gradle" / "libs.versions.toml").write_text("[versions]\n")

        found = _find_build_files(tmp_path)
        assert "gradle/libs.versions.toml" in found

    def test_finds_multiple_build_files(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle.kts").write_text("")
        (tmp_path / "settings.gradle.kts").write_text("")

        found = _find_build_files(tmp_path)
        assert "build.gradle.kts" in found
        assert "settings.gradle.kts" in found

    def test_missing_files_not_included(self, tmp_path: Path) -> None:
        found = _find_build_files(tmp_path)
        assert found == []


# ---------------------------------------------------------------------------
# analyze_case — patched integration test
# ---------------------------------------------------------------------------


class TestAnalyseCase:
    """Light integration: patch DB and run_static_analysis, verify bundle mutations."""

    def _make_bundle(self, case_id: str = "case-001") -> MagicMock:
        from kmp_repair_pipeline.domain.events import (
            DependencyUpdateEvent,
            UpdateClass,
            VersionChange,
        )
        from kmp_repair_pipeline.case_bundle.bundle import CaseBundle, CaseMeta
        from kmp_repair_pipeline.case_bundle.evidence import UpdateEvidence

        bundle = CaseBundle(
            meta=CaseMeta(
                case_id=case_id,
                event_id="ev-001",
                repository_url="https://github.com/test/repo",
                status="EXECUTED",
            )
        )
        bundle.update_evidence = UpdateEvidence(
            update_event=DependencyUpdateEvent(repo_url="https://github.com/test/repo"),
            version_changes=[
                VersionChange(
                    dependency_group="io.ktor",
                    version_key="ktor",
                    before="3.1.3",
                    after="3.4.1",
                )
            ],
            update_class=UpdateClass.DIRECT_LIBRARY,
        )
        return bundle

    def test_analyze_case_sets_status_analyzed(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.static_analysis.structural_builder import analyze_case

        case_id = "case-001"
        bundle = self._make_bundle(case_id)

        # Fake after-clone with one Kotlin file
        repo_dir = tmp_path / "after"
        repo_dir.mkdir()
        kt_dir = repo_dir / "src" / "commonMain" / "kotlin"
        kt_dir.mkdir(parents=True)
        kt_file = kt_dir / "App.kt"
        kt_file.write_text(
            'package com.example\nimport io.ktor.client.HttpClient\nclass App\n'
        )

        session = MagicMock()

        # Mock DB interactions
        with (
            patch("kmp_repair_pipeline.static_analysis.structural_builder.from_db_case",
                  return_value=bundle),
            patch("kmp_repair_pipeline.static_analysis.structural_builder.to_db"),
            patch("kmp_repair_pipeline.static_analysis.structural_builder.RevisionRepo") as MockRevRepo,
            patch("kmp_repair_pipeline.static_analysis.structural_builder.SourceEntityRepo"),
            patch("kmp_repair_pipeline.static_analysis.structural_builder.RepairCaseRepo") as MockCaseRepo,
            patch("kmp_repair_pipeline.static_analysis.structural_builder._persist_expect_actual_links"),
        ):
            after_rev = MagicMock()
            after_rev.local_path = str(repo_dir)
            MockRevRepo.return_value.get.return_value = after_rev

            case_row = MagicMock()
            MockCaseRepo.return_value.get_by_id.return_value = case_row

            result = analyze_case(case_id=case_id, session=session)

        assert bundle.meta.status == "ANALYZED"
        assert bundle.structural is not None
        assert result.total_kotlin_files >= 1
        assert result.impact_graphs[0].dependency_group == "io.ktor"

    def test_analyze_case_raises_if_no_after_revision(self) -> None:
        from kmp_repair_pipeline.static_analysis.structural_builder import analyze_case

        bundle = self._make_bundle()
        session = MagicMock()

        with (
            patch("kmp_repair_pipeline.static_analysis.structural_builder.from_db_case",
                  return_value=bundle),
            patch("kmp_repair_pipeline.static_analysis.structural_builder.RevisionRepo") as MockRevRepo,
        ):
            MockRevRepo.return_value.get.return_value = None
            with pytest.raises(ValueError, match="after revision"):
                analyze_case("case-001", session)
