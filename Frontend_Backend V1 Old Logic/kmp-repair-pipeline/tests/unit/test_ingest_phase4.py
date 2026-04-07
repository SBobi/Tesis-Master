"""Unit tests for Phase 4 ingest modules.

Tests for:
- event_classifier  (extended pattern tables)
- pr_fetcher        (parse_pr_url, PRFetchResult helpers)
- event_builder     (skip logic)
- repo_discoverer   (_is_dependabot_pr heuristic)
"""

from __future__ import annotations

import pytest

from kmp_repair_pipeline.domain.events import UpdateClass, VersionChange
from kmp_repair_pipeline.ingest.event_classifier import (
    classify_all,
    classify_update,
    dominant_class,
)
from kmp_repair_pipeline.ingest.github_client import parse_pr_url
from kmp_repair_pipeline.ingest.pr_fetcher import PRFetchResult, PRFile


# ---------------------------------------------------------------------------
# parse_pr_url
# ---------------------------------------------------------------------------


class TestParsePrUrl:
    def test_standard_url(self) -> None:
        owner, repo, num = parse_pr_url("https://github.com/foo/bar/pull/42")
        assert owner == "foo"
        assert repo == "bar"
        assert num == 42

    def test_trailing_slash(self) -> None:
        owner, repo, num = parse_pr_url("https://github.com/owner/myrepo/pull/7/")
        assert owner == "owner"
        assert repo == "myrepo"
        assert num == 7

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_pr_url("https://github.com/foo/bar")

    def test_invalid_number_raises(self) -> None:
        with pytest.raises((ValueError, IndexError)):
            parse_pr_url("https://github.com/foo/bar/pull/notanumber")


# ---------------------------------------------------------------------------
# PRFetchResult helpers
# ---------------------------------------------------------------------------


class TestPRFetchResult:
    def _make_result(self) -> PRFetchResult:
        return PRFetchResult(
            owner="acme",
            repo="kmp-app",
            number=5,
            title="Bump ktor from 3.1.3 to 3.4.1",
            body="",
            state="open",
            head_sha="abc123",
            base_sha="def456",
            head_ref="dependabot/gradle/ktor-3.4.1",
            base_ref="main",
            files=[
                PRFile("gradle/libs.versions.toml", "modified", 2, 2),
                PRFile("src/commonMain/kotlin/App.kt", "modified", 5, 3),
            ],
        )

    def test_pr_ref(self) -> None:
        r = self._make_result()
        assert r.pr_ref == "pull/5"

    def test_catalog_files_changed(self) -> None:
        r = self._make_result()
        assert r.catalog_files_changed == ["gradle/libs.versions.toml"]

    def test_no_catalog_changed(self) -> None:
        r = self._make_result()
        r.files = [PRFile("README.md", "modified", 1, 1)]
        assert r.catalog_files_changed == []


# ---------------------------------------------------------------------------
# event_classifier — extended tables
# ---------------------------------------------------------------------------


class TestEventClassifierExtended:
    def _vc(self, group: str, key: str = "dep") -> VersionChange:
        return VersionChange(dependency_group=group, version_key=key, before="1.0", after="2.0")

    # PLUGIN_TOOLCHAIN
    def test_agp(self) -> None:
        assert classify_update(self._vc("com.android.application", "agp")) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_ksp(self) -> None:
        assert classify_update(self._vc("com.google.devtools.ksp")) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_kotlin_version_key(self) -> None:
        # Even if group is unusual, version key starts with "kotlin"
        vc = VersionChange(dependency_group="some.plugin", version_key="kotlin-stdlib",
                           before="1.9", after="2.0")
        assert classify_update(vc) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_compose_plugin(self) -> None:
        assert classify_update(self._vc("org.jetbrains.compose")) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_ben_manes_versions(self) -> None:
        assert classify_update(self._vc("com.github.ben-manes.versions")) == UpdateClass.PLUGIN_TOOLCHAIN

    # PLATFORM_INTEGRATION
    def test_cocoapods_group(self) -> None:
        assert classify_update(self._vc("io.cocoapods.AlamoFire")) == UpdateClass.PLATFORM_INTEGRATION

    def test_apple_group(self) -> None:
        assert classify_update(self._vc("com.apple.swift-numerics")) == UpdateClass.PLATFORM_INTEGRATION

    def test_ios_pod_group(self) -> None:
        # A pure CocoaPods/iOS bridge group (not a Kotlin compiler artifact)
        assert classify_update(self._vc("co.touchlab.iosbridge")) == UpdateClass.PLATFORM_INTEGRATION

    # TRANSITIVE / BOM
    def test_bom_group(self) -> None:
        assert classify_update(self._vc("io.ktor:ktor-bom")) == UpdateClass.TRANSITIVE

    def test_bom_version_key(self) -> None:
        vc = VersionChange(dependency_group="io.ktor", version_key="bom-ktor",
                           before="3.1", after="3.2")
        assert classify_update(vc) == UpdateClass.TRANSITIVE

    def test_platform_version_key(self) -> None:
        vc = VersionChange(dependency_group="some.lib", version_key="platform-core",
                           before="1.0", after="1.1")
        assert classify_update(vc) == UpdateClass.TRANSITIVE

    # DIRECT_LIBRARY
    def test_ktor_direct(self) -> None:
        assert classify_update(self._vc("io.ktor")) == UpdateClass.DIRECT_LIBRARY

    def test_koin_direct(self) -> None:
        assert classify_update(self._vc("io.insert-koin:koin-core")) == UpdateClass.DIRECT_LIBRARY


class TestClassifyAll:
    def test_classify_all_returns_mapping(self) -> None:
        changes = [
            VersionChange(dependency_group="io.ktor", version_key="ktor", before="3.0", after="3.1"),
            VersionChange(dependency_group="org.jetbrains.kotlin.multiplatform",
                          version_key="kotlin", before="1.9", after="2.0"),
        ]
        result = classify_all(changes)
        assert result["io.ktor"] == UpdateClass.DIRECT_LIBRARY
        assert result["org.jetbrains.kotlin.multiplatform"] == UpdateClass.PLUGIN_TOOLCHAIN


class TestDominantClass:
    def test_plugin_beats_direct(self) -> None:
        assert dominant_class([UpdateClass.DIRECT_LIBRARY, UpdateClass.PLUGIN_TOOLCHAIN]) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_plugin_beats_platform(self) -> None:
        assert dominant_class([UpdateClass.PLATFORM_INTEGRATION, UpdateClass.PLUGIN_TOOLCHAIN]) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_platform_beats_direct(self) -> None:
        assert dominant_class([UpdateClass.DIRECT_LIBRARY, UpdateClass.PLATFORM_INTEGRATION]) == UpdateClass.PLATFORM_INTEGRATION

    def test_single_item(self) -> None:
        assert dominant_class([UpdateClass.TRANSITIVE]) == UpdateClass.TRANSITIVE

    def test_empty_list_returns_unknown(self) -> None:
        assert dominant_class([]) == UpdateClass.UNKNOWN


# ---------------------------------------------------------------------------
# repo_discoverer — _is_dependabot_pr heuristic
# ---------------------------------------------------------------------------


class TestIsDependabotPr:
    def _pr(self, login: str, title: str, labels: list[str]) -> tuple[dict, list[str]]:
        return {"user": {"login": login}, "title": title, "labels": [{"name": l} for l in labels]}, labels

    def test_dependabot_bot_login(self) -> None:
        from kmp_repair_pipeline.ingest.repo_discoverer import _is_dependabot_pr
        pr, labels = self._pr("dependabot[bot]", "Bump ktor", [])
        assert _is_dependabot_pr(pr, labels) is True

    def test_bump_title_with_label(self) -> None:
        from kmp_repair_pipeline.ingest.repo_discoverer import _is_dependabot_pr
        pr, labels = self._pr("renovate[bot]", "Bump ktor from 3.1 to 3.4", ["dependencies"])
        assert _is_dependabot_pr(pr, labels) is True

    def test_human_pr_not_matched(self) -> None:
        from kmp_repair_pipeline.ingest.repo_discoverer import _is_dependabot_pr
        pr, labels = self._pr("alice", "Add new feature", ["enhancement"])
        assert _is_dependabot_pr(pr, labels) is False
