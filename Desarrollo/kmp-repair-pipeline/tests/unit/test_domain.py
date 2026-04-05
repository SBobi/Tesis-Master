"""Unit tests for domain models."""

from __future__ import annotations

import pytest

from kmp_repair_pipeline.domain.events import DependencyUpdateEvent, UpdateClass, VersionChange
from kmp_repair_pipeline.domain.analysis import (
    DeclarationKind,
    ExpectActualPair,
    FileImpact,
    FileParseResult,
    ImpactGraph,
    ImpactRelation,
    KotlinDeclaration,
    SourceMetrics,
)
from kmp_repair_pipeline.domain.validation import ValidationStatus


class TestVersionChange:
    def test_round_trip(self) -> None:
        vc = VersionChange(dependency_group="io.ktor", version_key="ktor", before="2.3.0", after="2.3.5")
        assert vc.model_dump()["before"] == "2.3.0"

    def test_json_round_trip(self) -> None:
        vc = VersionChange(dependency_group="io.ktor", version_key="ktor", before="2.3.0", after="2.3.5")
        restored = VersionChange.model_validate_json(vc.model_dump_json())
        assert restored == vc


class TestDependencyUpdateEvent:
    def test_defaults(self) -> None:
        e = DependencyUpdateEvent(repo_url="https://github.com/example/repo")
        assert e.update_class == UpdateClass.UNKNOWN
        assert e.version_changes == []

    def test_with_changes(self) -> None:
        vc = VersionChange(dependency_group="io.ktor", version_key="ktor", before="2.3.0", after="2.3.5")
        e = DependencyUpdateEvent(
            repo_url="https://github.com/example/repo",
            version_changes=[vc],
            update_class=UpdateClass.DIRECT_LIBRARY,
        )
        assert len(e.version_changes) == 1
        assert e.update_class == UpdateClass.DIRECT_LIBRARY


class TestValidationStatus:
    def test_all_statuses_defined(self) -> None:
        expected = {
            "SUCCESS_REPOSITORY_LEVEL",
            "PARTIAL_SUCCESS",
            "FAILED_BUILD",
            "FAILED_TESTS",
            "NOT_RUN_ENVIRONMENT_UNAVAILABLE",
            "INCONCLUSIVE",
            "NOT_RUN_YET",
        }
        actual = {s.value for s in ValidationStatus}
        assert expected == actual


class TestImpactGraph:
    def test_empty_graph(self) -> None:
        g = ImpactGraph(
            dependency_group="io.ktor",
            version_before="2.3.0",
            version_after="2.3.5",
        )
        assert g.total_impacted == 0
        assert g.seed_files == []

    def test_with_impacted_files(self) -> None:
        fi = FileImpact(
            file_path="src/commonMain/kotlin/App.kt",
            relation=ImpactRelation.DIRECT,
            source_set="common",
        )
        g = ImpactGraph(
            dependency_group="io.ktor",
            version_before="2.3.0",
            version_after="2.3.5",
            seed_files=["src/commonMain/kotlin/App.kt"],
            impacted_files=[fi],
            total_impacted=1,
        )
        assert g.total_impacted == 1
        assert g.impacted_files[0].relation == ImpactRelation.DIRECT


class TestExpectActualPair:
    def test_pair_with_actuals(self) -> None:
        pair = ExpectActualPair(
            expect_fqcn="com.example.Platform",
            expect_file="src/commonMain/kotlin/Platform.kt",
            actual_files=[
                "src/androidMain/kotlin/Platform.kt",
                "src/iosMain/kotlin/Platform.kt",
            ],
        )
        assert len(pair.actual_files) == 2
