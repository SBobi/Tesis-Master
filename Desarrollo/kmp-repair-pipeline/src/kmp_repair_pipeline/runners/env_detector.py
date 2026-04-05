"""Detect the build environment and available compilation targets.

Returns an `EnvProfile` that records:
  - Java version and home
  - Whether gradlew is executable
  - Android SDK availability
  - Xcode availability (macOS only)
  - Which KMP targets are executable in this environment

This drives the `NOT_RUN_ENVIRONMENT_UNAVAILABLE` validation logic —
we never lie about which targets actually ran.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..utils.log import get_logger

log = get_logger(__name__)


@dataclass
class EnvProfile:
    """Snapshot of the build environment at detection time."""

    # Java
    java_available: bool = False
    java_version: str = ""
    java_home: str = ""

    # Gradle Wrapper
    gradlew_available: bool = False
    gradle_wrapper_version: str = ""

    # Android SDK
    android_sdk_available: bool = False
    android_sdk_root: str = ""
    android_build_tools_version: str = ""

    # Apple toolchain
    xcode_available: bool = False
    xcode_version: str = ""
    is_macos: bool = False

    # Derived target availability
    runnable_targets: list[str] = field(default_factory=list)
    unavailable_targets: dict[str, str] = field(default_factory=dict)
    # {"ios": "Xcode not found", ...}

    # Extra provenance
    os_name: str = ""
    os_version: str = ""
    python_version: str = ""

    def as_metadata_dict(self) -> dict:
        """Compact dict for storage in execution_runs.env_metadata (JSONB)."""
        return {
            "java_available": self.java_available,
            "java_version": self.java_version,
            "java_home": self.java_home,
            "gradlew_available": self.gradlew_available,
            "gradle_wrapper_version": self.gradle_wrapper_version,
            "android_sdk_available": self.android_sdk_available,
            "android_sdk_root": self.android_sdk_root,
            "xcode_available": self.xcode_available,
            "xcode_version": self.xcode_version,
            "is_macos": self.is_macos,
            "runnable_targets": self.runnable_targets,
            "unavailable_targets": self.unavailable_targets,
            "os_name": self.os_name,
            "os_version": self.os_version,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(repo_path: Path | str) -> EnvProfile:
    """Detect the build environment for the repository at `repo_path`."""
    repo = Path(repo_path)
    profile = EnvProfile(
        is_macos=platform.system() == "Darwin",
        os_name=platform.system(),
        os_version=platform.release(),
        python_version=platform.python_version(),
    )

    _detect_java(profile)
    _detect_gradlew(profile, repo)
    _detect_android_sdk(profile, repo)
    _detect_xcode(profile)
    # Ensure local.properties carries sdk.dir so Gradle can find the Android SDK
    # even without ANDROID_HOME in the shell environment.
    if profile.android_sdk_available and profile.android_sdk_root:
        _write_local_properties(repo, profile.android_sdk_root)
    _compute_runnable_targets(profile)

    log.info(
        "EnvProfile: java=%s gradlew=%s android=%s xcode=%s targets=%s",
        profile.java_version or "N/A",
        profile.gradlew_available,
        profile.android_sdk_available,
        profile.xcode_available,
        profile.runnable_targets,
    )
    return profile


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _detect_java(profile: EnvProfile) -> None:
    java_home = os.environ.get("JAVA_HOME", "")
    java_exe = shutil.which("java")

    if not java_exe and java_home:
        candidate = Path(java_home) / "bin" / "java"
        if candidate.exists():
            java_exe = str(candidate)

    if not java_exe:
        log.warning("java not found in PATH or JAVA_HOME")
        return

    try:
        result = subprocess.run(
            [java_exe, "-version"],
            capture_output=True, text=True, timeout=10,
        )
        # `java -version` writes to stderr
        version_output = result.stderr or result.stdout
        version_line = version_output.splitlines()[0] if version_output else ""
        profile.java_available = True
        profile.java_version = version_line.strip()
        profile.java_home = java_home or str(Path(java_exe).parent.parent)
    except Exception as exc:
        log.warning("java version check failed: %s", exc)


def _detect_gradlew(profile: EnvProfile, repo: Path) -> None:
    gradlew = repo / "gradlew"
    if not gradlew.exists():
        log.warning("gradlew not found in %s", repo)
        return
    if not os.access(gradlew, os.X_OK):
        # Make it executable
        try:
            gradlew.chmod(0o755)
        except OSError:
            log.warning("Could not chmod gradlew: %s", gradlew)
            return

    profile.gradlew_available = True

    # Try to get Gradle version (quick, uses wrapper)
    try:
        result = subprocess.run(
            [str(gradlew), "--version", "--no-daemon"],
            capture_output=True, text=True, timeout=60,
            cwd=str(repo),
        )
        for line in result.stdout.splitlines():
            if line.startswith("Gradle "):
                profile.gradle_wrapper_version = line.strip()
                break
    except Exception as exc:
        log.debug("gradlew --version failed: %s", exc)


def _detect_android_sdk(profile: EnvProfile, repo: Path | None = None) -> None:
    sdk_root = (
        os.environ.get("ANDROID_HOME")
        or os.environ.get("ANDROID_SDK_ROOT")
        or ""
    )
    if not sdk_root:
        # Common macOS location
        default = Path.home() / "Library" / "Android" / "sdk"
        if default.exists():
            sdk_root = str(default)

    # Android also supports sdk.dir in local.properties at the repo root.
    # Gradle reads this file automatically — we do the same so the pipeline
    # can resolve the SDK path even when environment variables are not set.
    if not sdk_root and repo is not None:
        sdk_root = _read_sdk_dir_from_local_properties(repo)

    if not sdk_root or not Path(sdk_root).exists():
        log.info("Android SDK not found")
        return

    profile.android_sdk_available = True
    profile.android_sdk_root = sdk_root

    # Try to find build-tools version
    build_tools_dir = Path(sdk_root) / "build-tools"
    if build_tools_dir.exists():
        versions = sorted(build_tools_dir.iterdir(), reverse=True)
        if versions:
            profile.android_build_tools_version = versions[0].name


def _detect_xcode(profile: EnvProfile) -> None:
    if not profile.is_macos:
        return
    xcodebuild = shutil.which("xcodebuild")
    if not xcodebuild:
        return
    try:
        result = subprocess.run(
            ["xcodebuild", "-version"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            profile.xcode_available = True
            first_line = result.stdout.splitlines()[0] if result.stdout else ""
            profile.xcode_version = first_line.strip()
    except Exception as exc:
        log.debug("xcodebuild version check failed: %s", exc)


def _read_sdk_dir_from_local_properties(repo: Path) -> str:
    """Read sdk.dir from local.properties at the project root.

    Gradle reads this file automatically when ANDROID_HOME is not set.
    The pipeline mirrors this behaviour so the env detector can resolve
    the SDK path the same way Gradle would during an actual build.

    Returns the path string if found and non-empty, otherwise "".
    """
    local_props = repo / "local.properties"
    if not local_props.exists():
        return ""
    try:
        for line in local_props.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("sdk.dir="):
                sdk_dir = line.split("=", 1)[1].strip()
                if sdk_dir:
                    log.info("Android SDK from local.properties: %s", sdk_dir)
                    return sdk_dir
    except OSError as exc:
        log.debug("Could not read local.properties: %s", exc)
    return ""


def _write_local_properties(repo: Path, sdk_root: str) -> None:
    """Write or update local.properties with sdk.dir for Gradle.

    Called by the pipeline when ANDROID_HOME is set but local.properties
    is absent or has a stale sdk.dir value.  This is the standard way to
    configure the Android SDK path for projects that don't commit
    local.properties (most do not — it is gitignored by default).

    Only writes if the current value differs from `sdk_root` to avoid
    unnecessary git dirtiness.
    """
    local_props = repo / "local.properties"
    target_line = f"sdk.dir={sdk_root}"

    if local_props.exists():
        content = local_props.read_text(encoding="utf-8")
        # Already has the correct value — no-op
        if f"sdk.dir={sdk_root}" in content:
            return
        # Update existing sdk.dir line
        import re as _re
        updated = _re.sub(r"^sdk\.dir=.*$", target_line, content, flags=_re.MULTILINE)
        if "sdk.dir=" not in updated:
            updated = updated.rstrip("\n") + "\n" + target_line + "\n"
        local_props.write_text(updated, encoding="utf-8")
    else:
        local_props.write_text(target_line + "\n", encoding="utf-8")

    log.info("Wrote sdk.dir to %s", local_props)


def _compute_runnable_targets(profile: EnvProfile) -> None:
    """Populate runnable_targets and unavailable_targets based on detected tools."""
    if not profile.java_available:
        profile.unavailable_targets["shared"] = "Java not found"
        profile.unavailable_targets["android"] = "Java not found"
        profile.unavailable_targets["ios"] = "Java not found"
        return

    if not profile.gradlew_available:
        profile.unavailable_targets["shared"] = "gradlew not found in repository"
        profile.unavailable_targets["android"] = "gradlew not found in repository"
        profile.unavailable_targets["ios"] = "gradlew not found in repository"
        return

    # Shared (commonMain) always runnable if Java + gradlew present
    profile.runnable_targets.append("shared")

    # Android requires Android SDK
    if profile.android_sdk_available:
        profile.runnable_targets.append("android")
    else:
        profile.unavailable_targets["android"] = (
            "Android SDK not found (set ANDROID_HOME or ANDROID_SDK_ROOT)"
        )

    # iOS requires macOS + Xcode
    if profile.xcode_available:
        profile.runnable_targets.append("ios")
    else:
        reason = (
            "Xcode not found"
            if profile.is_macos
            else "iOS builds require macOS"
        )
        profile.unavailable_targets["ios"] = reason
