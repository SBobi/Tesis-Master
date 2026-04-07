"""Classify a dependency update event into a thesis UpdateClass.

Classification order (first match wins):
  1. PLUGIN_TOOLCHAIN   — Gradle plugins, Kotlin compiler, AGP, build tooling
  2. PLATFORM_INTEGRATION — CocoaPods, SPM, Apple/iOS/watchOS/tvOS integrations
  3. TRANSITIVE         — declared as a BOM/platform constraint, or no direct toml entry
  4. DIRECT_LIBRARY     — everything else
"""

from __future__ import annotations

from ..domain.events import UpdateClass, VersionChange

# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# Group-ID substrings that indicate a Gradle plugin or build toolchain entry.
# Checked case-insensitively against the full dependency_group string.
_PLUGIN_TOOLCHAIN_KEYWORDS: tuple[str, ...] = (
    # Android Gradle Plugin
    "com.android.application",
    "com.android.library",
    "com.android.tools.build",
    "android.tools",
    "agp",
    # Kotlin compiler / plugins
    "org.jetbrains.kotlin",
    "kotlin-gradle-plugin",
    "kotlin.gradle.plugin",
    # Gradle itself
    "gradle",
    "com.gradle",
    "org.gradle",
    # KSP / KAPT
    "com.google.devtools.ksp",
    "kotlin-ksp",
    # Compose Multiplatform Gradle plugin
    "org.jetbrains.compose",
    # Dependency management plugins
    "com.github.ben-manes.versions",
    "nl.littlerobots.version-catalog-update",
    "io.gitlab.arturbosch.detekt",
    "org.jlleitschuh.gradle.ktlint",
    "com.google.gms.google-services",
    "com.google.firebase.crashlytics",
    "com.google.firebase.appdistribution",
    "io.sentry.android.gradle",
    # SQLDelight / Room code-gen plugins
    "app.cash.sqldelight",
    # Serialization
    "plugin.serialization",
)

# Group-ID substrings that indicate an Apple/iOS/watchOS/tvOS platform bridge.
_PLATFORM_INTEGRATION_KEYWORDS: tuple[str, ...] = (
    "cocoapods",
    "cocoapod",
    "swift",
    "apple",
    "ios",
    "watchos",
    "tvos",
    "macos",
    "xcframework",
    "swiftpackage",
    "spm",
)

# Version-key prefixes that strongly signal a BOM / platform constraint.
_TRANSITIVE_VERSION_KEY_PREFIXES: tuple[str, ...] = (
    "bom",
    "platform",
    "constraints",
)

# Group-ID substrings for known BOM / platform families.
_TRANSITIVE_GROUP_KEYWORDS: tuple[str, ...] = (
    "-bom",
    ".bom",
    ":bom",
    "bill-of-materials",
    "platform(",  # Gradle platform() notation leaks into group strings sometimes
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_update(
    version_change: VersionChange,
    build_file_paths: list[str] | None = None,
) -> UpdateClass:
    """Classify one VersionChange into an UpdateClass.

    Parameters
    ----------
    version_change:
        The dependency that changed.
    build_file_paths:
        Optional list of build-file paths present in the repo (for future
        build-file-content heuristics; currently unused beyond presence check).
    """
    group = version_change.dependency_group.lower()
    vkey = version_change.version_key.lower()

    # 1. Plugin / toolchain
    if _matches_any(group, _PLUGIN_TOOLCHAIN_KEYWORDS):
        return UpdateClass.PLUGIN_TOOLCHAIN

    # Also classify as PLUGIN_TOOLCHAIN if the version_key is "agp" / "kotlin*" /
    # "gradle*" — some catalogs store these under non-obvious group names.
    if any(
        vkey.startswith(pfx)
        for pfx in ("agp", "kotlin", "gradle", "ksp", "compose-plugin")
    ):
        return UpdateClass.PLUGIN_TOOLCHAIN

    # 2. Platform integration
    if _matches_any(group, _PLATFORM_INTEGRATION_KEYWORDS):
        return UpdateClass.PLATFORM_INTEGRATION

    # 3. Transitive / BOM constraint
    if _matches_any(group, _TRANSITIVE_GROUP_KEYWORDS):
        return UpdateClass.TRANSITIVE
    if any(vkey.startswith(pfx) for pfx in _TRANSITIVE_VERSION_KEY_PREFIXES):
        return UpdateClass.TRANSITIVE

    # 4. Default: direct library
    return UpdateClass.DIRECT_LIBRARY


def classify_all(
    version_changes: list[VersionChange],
    build_file_paths: list[str] | None = None,
) -> dict[str, UpdateClass]:
    """Classify a list of changes; returns {dependency_group: UpdateClass}."""
    return {
        vc.dependency_group: classify_update(vc, build_file_paths)
        for vc in version_changes
    }


def dominant_class(classes: list[UpdateClass]) -> UpdateClass:
    """Return the most-impactful class in a list (used when a PR has mixed updates).

    Priority: PLUGIN_TOOLCHAIN > PLATFORM_INTEGRATION > DIRECT_LIBRARY > TRANSITIVE > UNKNOWN
    """
    priority = [
        UpdateClass.PLUGIN_TOOLCHAIN,
        UpdateClass.PLATFORM_INTEGRATION,
        UpdateClass.DIRECT_LIBRARY,
        UpdateClass.TRANSITIVE,
        UpdateClass.UNKNOWN,
    ]
    for cls in priority:
        if cls in classes:
            return cls
    return UpdateClass.UNKNOWN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matches_any(value: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in value for kw in keywords)
