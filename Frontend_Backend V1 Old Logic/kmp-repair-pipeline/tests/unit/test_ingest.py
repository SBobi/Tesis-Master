"""Unit tests for ingest module — version catalog parsing and event detection."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kmp_repair_pipeline.ingest.toml_parser import VersionCatalog
from kmp_repair_pipeline.ingest.version_catalog import detect_version_changes
from kmp_repair_pipeline.ingest.event_classifier import classify_update
from kmp_repair_pipeline.domain.events import UpdateClass, VersionChange


SAMPLE_TOML = textwrap.dedent("""\
    [versions]
    ktor = "2.3.0"
    kotlin = "1.9.0"
    compose = "1.5.0"

    [libraries]
    ktor-core = { module = "io.ktor:ktor-client-core", version.ref = "ktor" }
    ktor-android = { module = "io.ktor:ktor-client-android", version.ref = "ktor" }
    compose-ui = { module = "androidx.compose.ui:ui", version.ref = "compose" }

    [plugins]
    kotlin-multiplatform = { id = "org.jetbrains.kotlin.multiplatform", version.ref = "kotlin" }
""")


@pytest.fixture()
def before_toml(tmp_path: Path) -> Path:
    p = tmp_path / "before" / "libs.versions.toml"
    p.parent.mkdir()
    p.write_text(SAMPLE_TOML)
    return p


@pytest.fixture()
def after_toml(tmp_path: Path) -> Path:
    updated = SAMPLE_TOML.replace('ktor = "2.3.0"', 'ktor = "2.3.5"')
    p = tmp_path / "after" / "libs.versions.toml"
    p.parent.mkdir()
    p.write_text(updated)
    return p


class TestVersionCatalog:
    def test_parse_versions(self, before_toml: Path) -> None:
        catalog = VersionCatalog(before_toml)
        assert catalog.versions["ktor"] == "2.3.0"
        assert catalog.versions["kotlin"] == "1.9.0"

    def test_parse_libraries(self, before_toml: Path) -> None:
        catalog = VersionCatalog(before_toml)
        assert "ktor-core" in catalog.libraries
        assert catalog.libraries["ktor-core"]["group"] == "io.ktor"
        assert catalog.libraries["ktor-core"]["version_ref"] == "ktor"

    def test_parse_plugins(self, before_toml: Path) -> None:
        catalog = VersionCatalog(before_toml)
        assert "kotlin-multiplatform" in catalog.plugins
        assert catalog.plugins["kotlin-multiplatform"]["id"] == "org.jetbrains.kotlin.multiplatform"

    def test_find_version_key(self, before_toml: Path) -> None:
        catalog = VersionCatalog(before_toml)
        assert catalog.find_version_key("io.ktor") == "ktor"

    def test_set_version_updates_file(self, before_toml: Path) -> None:
        catalog = VersionCatalog(before_toml)
        catalog.set_version("ktor", "2.3.9")
        reloaded = VersionCatalog(before_toml)
        assert reloaded.versions["ktor"] == "2.3.9"

    def test_set_version_unknown_key_raises(self, before_toml: Path) -> None:
        catalog = VersionCatalog(before_toml)
        with pytest.raises(KeyError):
            catalog.set_version("nonexistent", "1.0.0")


class TestDetectVersionChanges:
    def test_detects_ktor_change(self, before_toml: Path, after_toml: Path) -> None:
        result = detect_version_changes(before_toml, after_toml)
        assert result.has_changes
        groups = {c.dependency_group for c in result.changes}
        assert "io.ktor" in groups

    def test_before_after_versions(self, before_toml: Path, after_toml: Path) -> None:
        result = detect_version_changes(before_toml, after_toml)
        ktor_change = next(c for c in result.changes if c.dependency_group == "io.ktor")
        assert ktor_change.before == "2.3.0"
        assert ktor_change.after == "2.3.5"

    def test_no_changes_when_identical(self, before_toml: Path) -> None:
        result = detect_version_changes(before_toml, before_toml)
        assert not result.has_changes

    def test_detects_changes_from_raw_toml_content(self, before_toml: Path, after_toml: Path) -> None:
        result = detect_version_changes(
            before_toml.read_text(encoding="utf-8"),
            after_toml.read_text(encoding="utf-8"),
        )
        assert result.has_changes
        assert any(c.dependency_group == "io.ktor" for c in result.changes)

    def test_string_paths_still_supported(self, before_toml: Path, after_toml: Path) -> None:
        result = detect_version_changes(str(before_toml), str(after_toml))
        assert result.has_changes


class TestEventClassifier:
    def test_classifies_plugin(self) -> None:
        vc = VersionChange(dependency_group="org.jetbrains.kotlin.multiplatform",
                           version_key="kotlin", before="1.9.0", after="2.0.0")
        assert classify_update(vc, []) == UpdateClass.PLUGIN_TOOLCHAIN

    def test_classifies_direct_library(self) -> None:
        vc = VersionChange(dependency_group="io.ktor",
                           version_key="ktor", before="2.3.0", after="2.3.5")
        assert classify_update(vc, []) == UpdateClass.DIRECT_LIBRARY

    def test_classifies_cocoapods(self) -> None:
        vc = VersionChange(dependency_group="cocoapods.SocketIO",
                           version_key="socketio", before="1.0", after="1.1")
        assert classify_update(vc, []) == UpdateClass.PLATFORM_INTEGRATION
