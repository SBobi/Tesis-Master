"""Parse Kotlin/Gradle compiler errors from raw stdout/stderr.

Produces `ErrorObservation` instances consumed by `ExecutionEvidence`.

Supported error patterns:
  - Kotlin compiler: "e: file.kt:42:10: error: Unresolved reference"
  - Kotlin compiler (new format): "e: error: ..." without file/line
  - Gradle task failure summary: "> Task :foo FAILED"
  - Gradle general error: "> Could not resolve ..."
  - Android resource compilation errors
  - Linker / symbol errors from native targets
  - Gradle/JVM init failures (e.g. Java version incompatibility)
  - KLIB ABI mismatch (error): "e: KLIB resolver: Could not find ...iosarm64/X.Y.Z/..."
    → classified as KLIB_ABI_ERROR with an actionable hint to check the
    `kotlin` version in gradle/libs.versions.toml
  - KLIB ABI mismatch (warning): "w: KLIB resolver: Skipping '...' having incompatible
    ABI version '2.3.0'. The library was produced by '2.3.0' compiler."
    → classified as KLIB_ABI_ERROR and the required Kotlin version is extracted
    and injected into the actionable hint so the LLM knows the exact target version
"""

from __future__ import annotations

import re
from typing import Optional

from ..case_bundle.evidence import ErrorObservation

# ---------------------------------------------------------------------------
# Compiled patterns (order matters — more specific first)
# ---------------------------------------------------------------------------

# Kotlin compiler error with file, line, column
# e: /path/to/File.kt: (42, 10): error: Unresolved reference: HttpClient
_KT_FILE_LINE = re.compile(
    r"^e:\s+(?P<file>[^\s:][^:]+\.kt)\s*:\s*\((?P<line>\d+),\s*(?P<col>\d+)\)\s*:\s*(?P<msg>.+)$",
    re.MULTILINE,
)

# Kotlin compiler error with file and line (no column)
# e: file.kt:42:10: error message
_KT_FILE_LINE_SIMPLE = re.compile(
    r"^e:\s+(?P<file>[^\s:][^:]+\.kt):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>.+)$",
    re.MULTILINE,
)

# KLIB ABI resolver failure (error line) — always parsed BEFORE _KT_NO_FILE.
# Matches lines like:
#   e: KLIB resolver: Could not find "/path/ktor-client-logging-iosarm64/3.4.1/..."
#   e: KLIB resolver: Skipping '/path/...': Incompatible ABI version. Expected '2.2', found '2.3'.
_KLIB_ABI = re.compile(
    r"^e:\s+KLIB resolver:\s+(?P<msg>[^\n]+)$",
    re.MULTILINE,
)

# KLIB ABI resolver warning — the Kotlin compiler emits a "w:" (warning) line
# that contains the EXACT required Kotlin version.  This is the most precise
# signal in the entire output: it tells us not just that there is an ABI
# mismatch, but precisely which compiler version produced the incompatible KLIB.
#
# Real example from compileKotlinIosArm64.stderr:
#   w: KLIB resolver: Skipping '.../ktor-client-logging-iosArm64Main-3.4.1.klib'
#      having incompatible ABI version '2.3.0'. The library was produced by
#      '2.3.0' compiler. The current Kotlin compiler can consume libraries having
#      ABI version <= '2.2.0'. Please upgrade your Kotlin compiler version to
#      consume this library.
#
# We extract the "produced by 'X.Y.Z' compiler" version and surface it as the
# required Kotlin version in the actionable hint.
_KLIB_ABI_WARNING = re.compile(
    r"^w:\s+KLIB resolver:\s+Skipping\s+['\"]?(?P<lib>[^'\"]+)['\"]?"
    r".*?produced by\s+['\"](?P<required_version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)['\"]",
    re.MULTILINE | re.DOTALL,
)

# Simpler fallback for w: KLIB lines that don't match the produced-by pattern
_KLIB_ABI_WARNING_SIMPLE = re.compile(
    r"^w:\s+KLIB resolver:\s+(?P<msg>[^\n]+)$",
    re.MULTILINE,
)

# JVM / common-metadata incompatibility errors.  Kotlin emits these when a
# JAR's .kotlin_module (or a class's metadata annotation) was written by a
# newer Kotlin compiler than the one currently in use.
#
# Two surface forms:
#
# (a) .kotlin_module inside a JAR — tells us the REQUIRED version verbatim:
#   e: file:///…/ktor-client-core-jvm-3.4.1.jar!/META-INF/ktor-client-core.kotlin_module
#      Module was compiled with an incompatible version of Kotlin.
#      The binary version of its metadata is 2.3.0, expected version is 2.1.0.
#
# (b) In-source class reference — same root cause, different wording:
#   e: file:///…/FeedStore.kt:38:23 Class 'kotlin.coroutines.CoroutineContext'
#      was compiled with an incompatible version of Kotlin.
#      The actual metadata version is 2.3.0, but the compiler version 2.1.0
#      can read versions up to 2.2.0.
#
# We extract the HIGHER version (the one we NEED to upgrade to) from both forms
# and record it as `required_kotlin_version`.  This is the most precise
# JVM-side signal: it tells us exactly which Kotlin the dependency was compiled
# with, so the RepairAgent knows the correct bump target.
_KOTLIN_METADATA_VERSION_JAR = re.compile(
    r"The binary version of its metadata is\s+(?P<required>[0-9]+\.[0-9]+(?:\.[0-9]+)?)",
    re.MULTILINE,
)
_KOTLIN_METADATA_VERSION_SRC = re.compile(
    r"The actual metadata version is\s+(?P<required>[0-9]+\.[0-9]+(?:\.[0-9]+)?)",
    re.MULTILINE,
)

# Kotlin compiler warning/error without file location
# e: error: ...
_KT_NO_FILE = re.compile(
    r"^e:\s+(?:error:\s*)?(?P<msg>[^\n]+)$",
    re.MULTILINE,
)

# Gradle "Could not resolve dependency" errors
_GRADLE_RESOLVE = re.compile(
    r">\s*Could not resolve\s+(?P<dep>[^\n]+)",
    re.MULTILINE,
)

# Gradle "Could not find" dependency errors
_GRADLE_NOT_FOUND = re.compile(
    r">\s*Could not find\s+(?P<dep>[^\n]+)",
    re.MULTILINE,
)

# Gradle task failure marker
_GRADLE_TASK_FAILED = re.compile(
    r">\s*Task\s+(?P<task>:\S+)\s+FAILED",
    re.MULTILINE,
)

# Android AAPT2 errors: /path/res/layout/file.xml:5: error: ...
_AAPT_ERROR = re.compile(
    r"^(?P<file>[^\s:]+\.xml):(?P<line>\d+):\s*error:\s*(?P<msg>.+)$",
    re.MULTILINE,
)

# Generic "error:" lines that didn't match earlier patterns
_GENERIC_ERROR = re.compile(
    r"^(?:.*)?error:\s+(?P<msg>[^\n]{10,})$",
    re.MULTILINE | re.IGNORECASE,
)

# Gradle "* What went wrong:" section — catches JVM/init failures that have
# no "e:" or "error:" prefix (e.g. java.lang.IllegalArgumentException: 25.0.1
# caused by Kotlin compiler being unable to parse Java EA/LTS version strings).
_GRADLE_WHAT_WENT_WRONG = re.compile(
    r"\* What went wrong:\s*\n(?P<msg>[^\n]+)",
    re.MULTILINE,
)

# Java/JVM exception lines that indicate environment failures
_JVM_EXCEPTION = re.compile(
    r"^(?P<exc>java\.\S+Exception|org\.jetbrains\.\S+Exception):\s+(?P<msg>[^\n]+)$",
    re.MULTILINE,
)

# Transitive dependency conflict / diamond dependency
# Matches Gradle output like:
#   > Conflict with dependency 'com.squareup.okhttp3:okhttp' in project ':app'.
#   > Could not resolve com.example:lib:1.0.0. Multiple conflicting ...
#   > There was a conflict between ... versions
_DEPENDENCY_CONFLICT = re.compile(
    r"(?:Conflict with dependency\s+'(?P<dep>[^']+)'|"
    r"Multiple\s+conflicting\s+(?:versions|dependencies)[^\n]*(?P<dep2>[^\n]+))",
    re.MULTILINE | re.IGNORECASE,
)

# Gradle build-script / plugin API failure
# Matches lines like:
#   > Could not apply plugin [id: 'com.android.application']
#   > Script compilation error: ...
#   > An exception occurred applying plugin request [id: 'org.jetbrains.kotlin.android']
#   > Plugin [id: 'com.android.library'] was not found
_BUILD_SCRIPT_ERROR = re.compile(
    r"(?:Could not apply plugin\s+\[(?P<plugin>[^\]]+)\]"
    r"|An exception occurred applying plugin request\s+\[(?P<plugin2>[^\]]+)\]"
    r"|Plugin\s+\[(?P<plugin3>[^\]]+)\]\s+was not found"
    r"|Script compilation error:\s+(?P<msg>[^\n]+))",
    re.MULTILINE,
)

# API break — unresolved reference or type mismatch after a dependency update.
# Separate from the generic COMPILE_ERROR:  we extract the symbol name so the
# RepairAgent has a structured signal (not just a blob of compiler output).
# Examples:
#   e: Foo.kt:(12,5): error: Unresolved reference: HttpClient
#   e: Bar.kt:8:3: error: None of the following functions can be called with...
#   e: Baz.kt:(3,1): error: Type mismatch: inferred type is HttpClientConfig<*>
_UNRESOLVED_REF = re.compile(
    r"Unresolved reference:\s+(?P<symbol>\w+)",
    re.MULTILINE,
)
_TYPE_MISMATCH = re.compile(
    r"Type mismatch:\s+(?P<details>[^\n]{5,80})",
    re.MULTILINE,
)

# Maximum errors to extract per output stream (avoid pathological inputs)
_MAX_ERRORS = 50


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(stdout: str, stderr: str, parser_label: str = "regex") -> list[ErrorObservation]:
    """Parse error observations from combined Gradle/Kotlin output.

    Parameters
    ----------
    stdout:
        Captured standard output of the Gradle invocation.
    stderr:
        Captured standard error of the Gradle invocation.
    parser_label:
        Provenance label stored on each `ErrorObservation`.

    Returns
    -------
    Deduplicated list of `ErrorObservation` instances.
    """
    combined = stdout + "\n" + stderr
    observations: list[ErrorObservation] = []
    seen: set[str] = set()

    def add(obs: ErrorObservation) -> None:
        key = f"{obs.error_type}|{obs.file_path}|{obs.line}|{obs.message}"
        if key not in seen:
            seen.add(key)
            obs.parser = parser_label
            observations.append(obs)

    # 1. Kotlin file/line/column errors (most specific)
    for m in _KT_FILE_LINE.finditer(combined):
        add(ErrorObservation(
            error_type="COMPILE_ERROR",
            file_path=_normalise_path(m.group("file")),
            line=int(m.group("line")),
            column=int(m.group("col")),
            message=m.group("msg").strip(),
            raw_text=m.group(0),
        ))

    # 2. Kotlin simple file:line:col errors
    for m in _KT_FILE_LINE_SIMPLE.finditer(combined):
        add(ErrorObservation(
            error_type="COMPILE_ERROR",
            file_path=_normalise_path(m.group("file")),
            line=int(m.group("line")),
            column=int(m.group("col")),
            message=m.group("msg").strip(),
            raw_text=m.group(0),
        ))

    # 3. Dependency resolution failures
    for m in _GRADLE_RESOLVE.finditer(combined):
        add(ErrorObservation(
            error_type="DEPENDENCY_RESOLUTION_ERROR",
            message=f"Could not resolve {m.group('dep').strip()}",
            raw_text=m.group(0),
        ))

    for m in _GRADLE_NOT_FOUND.finditer(combined):
        add(ErrorObservation(
            error_type="DEPENDENCY_RESOLUTION_ERROR",
            message=f"Could not find {m.group('dep').strip()}",
            raw_text=m.group(0),
        ))

    # 4. Android AAPT resource errors
    for m in _AAPT_ERROR.finditer(combined):
        add(ErrorObservation(
            error_type="RESOURCE_ERROR",
            file_path=_normalise_path(m.group("file")),
            line=int(m.group("line")),
            message=m.group("msg").strip(),
            raw_text=m.group(0),
        ))

    # 5. KLIB ABI resolver failures — must come before _KT_NO_FILE so these
    #    "e: KLIB resolver: ..." lines are classified with an actionable hint
    #    instead of being silently swallowed as generic COMPILE_ERROR messages.
    for m in _KLIB_ABI.finditer(combined):
        raw_msg = m.group("msg").strip()
        actionable = (
            f"KLIB resolver: {raw_msg} "
            f"[KLIB_ABI_HINT: This error means the library's iOS KLIB was compiled "
            f"with a Kotlin version incompatible with the project's kotlin compiler. "
            f"ACTION: Check and update the 'kotlin' version alias in "
            f"gradle/libs.versions.toml to match the library's minimum Kotlin requirement.]"
        )
        add(ErrorObservation(
            error_type="KLIB_ABI_ERROR",
            message=actionable,
            raw_text=m.group(0),
        ))

    # 5b. KLIB ABI resolver WARNINGS (w: lines) — these carry the most precise
    #     signal in the entire build output: the exact Kotlin version that produced
    #     the incompatible KLIB.  We extract "produced by 'X.Y.Z' compiler" and
    #     inject it into the actionable hint so the RepairAgent knows the exact
    #     version to bump `kotlin` TO in gradle/libs.versions.toml.
    #
    #     Example w: line:
    #       w: KLIB resolver: Skipping '.../ktor-client-logging-iosArm64Main-3.4.1.klib'
    #          having incompatible ABI version '2.3.0'. The library was produced by
    #          '2.3.0' compiler. The current Kotlin compiler can consume libraries
    #          having ABI version <= '2.2.0'. Please upgrade your Kotlin compiler
    #          version to consume this library.
    #
    #     Extracted: required_version = "2.3.0"
    #     Injected hint: "REQUIRED KOTLIN VERSION: 2.3.0 — bump `kotlin` alias UP
    #     to '2.3.0' in gradle/libs.versions.toml"
    _klib_warn_seen: set[str] = set()
    for m in _KLIB_ABI_WARNING.finditer(combined):
        required_version = m.group("required_version").strip()
        lib_name = m.group("lib").strip().split("/")[-1]  # just the filename
        key = f"klib_warn|{required_version}|{lib_name}"
        if key in _klib_warn_seen:
            continue
        _klib_warn_seen.add(key)
        actionable = (
            f"KLIB resolver: Skipping '{lib_name}' — ABI version incompatible. "
            f"[KLIB_ABI_HINT: The library was produced by Kotlin '{required_version}' compiler. "
            f"REQUIRED KOTLIN VERSION: {required_version} — you MUST bump the `kotlin` alias "
            f"UP to '{required_version}' (or higher) in gradle/libs.versions.toml. "
            f"Do NOT downgrade kotlin. The fix is: kotlin = \"{required_version}\".]"
        )
        add(ErrorObservation(
            error_type="KLIB_ABI_ERROR",
            message=actionable,
            raw_text=m.group(0),
            required_kotlin_version=required_version,
        ))

    # 5c. Fallback for w: KLIB lines that didn't match the produced-by pattern
    for m in _KLIB_ABI_WARNING_SIMPLE.finditer(combined):
        raw_msg = m.group("msg").strip()
        # Skip if already captured by the richer pattern above
        if any(raw_msg[:40] in o.message for o in observations):
            continue
        actionable = (
            f"KLIB resolver (warning): {raw_msg} "
            f"[KLIB_ABI_HINT: KLIB ABI mismatch. "
            f"ACTION: Update the 'kotlin' version alias in gradle/libs.versions.toml.]"
        )
        add(ErrorObservation(
            error_type="KLIB_ABI_ERROR",
            message=actionable,
            raw_text=m.group(0),
        ))

    # 5d. JVM metadata / .kotlin_module incompatibility errors.
    #
    #     These arise when a dependency JAR was compiled with a NEWER Kotlin than
    #     the project's current compiler.  The error message contains the exact
    #     version we must upgrade to:
    #       "The binary version of its metadata is 2.3.0, expected version is 2.1.0."
    #       "The actual metadata version is 2.3.0, but the compiler version 2.1.0 …"
    #
    #     We extract `required_version` from EVERY match, deduplicate by
    #     (artifact_group, required_version), and emit one KLIB_ABI_ERROR per
    #     unique required version.  This ensures that "ktor 3.4.1 needs 2.3.0"
    #     and "koin 4.1.0 needs 2.1.20" are both surfaced so the consolidation
    #     step in bundle.py can pick max("2.3.0", "2.1.20") = "2.3.0".
    _metadata_versions_seen: set[str] = set()
    for m in _KOTLIN_METADATA_VERSION_JAR.finditer(combined):
        required_version = m.group("required").strip()
        if required_version in _metadata_versions_seen:
            continue
        _metadata_versions_seen.add(required_version)
        # Try to find the artifact name on the same line for context
        line_start = combined.rfind("\n", 0, m.start()) + 1
        line_end = combined.find("\n", m.end())
        if line_end < 0:
            line_end = len(combined)
        raw_line = combined[line_start:line_end].strip()
        actionable = (
            f"JVM metadata incompatibility: a dependency JAR was compiled with "
            f"Kotlin '{required_version}'. "
            f"[KOTLIN_METADATA_HINT: The project's Kotlin compiler is OLDER than "
            f"'{required_version}'. "
            f"REQUIRED KOTLIN VERSION: {required_version} — bump the `kotlin` alias "
            f"UP to '{required_version}' in gradle/libs.versions.toml. "
            f"Context: {raw_line[:120]}]"
        )
        add(ErrorObservation(
            error_type="KLIB_ABI_ERROR",
            message=actionable,
            raw_text=raw_line,
            required_kotlin_version=required_version,
        ))

    for m in _KOTLIN_METADATA_VERSION_SRC.finditer(combined):
        required_version = m.group("required").strip()
        if required_version in _metadata_versions_seen:
            continue
        _metadata_versions_seen.add(required_version)
        line_start = combined.rfind("\n", 0, m.start()) + 1
        line_end = combined.find("\n", m.end())
        if line_end < 0:
            line_end = len(combined)
        raw_line = combined[line_start:line_end].strip()
        actionable = (
            f"JVM class compiled with incompatible Kotlin '{required_version}'. "
            f"[KOTLIN_METADATA_HINT: REQUIRED KOTLIN VERSION: {required_version} — "
            f"bump `kotlin` alias UP in gradle/libs.versions.toml. "
            f"Context: {raw_line[:120]}]"
        )
        add(ErrorObservation(
            error_type="KLIB_ABI_ERROR",
            message=actionable,
            raw_text=raw_line,
            required_kotlin_version=required_version,
        ))

    # 5e. Transitive dependency conflicts (diamond dependency, version clash)
    for m in _DEPENDENCY_CONFLICT.finditer(combined):
        dep = (m.group("dep") or m.group("dep2") or "").strip()
        msg = f"Dependency conflict: {dep}" if dep else "Dependency conflict detected"
        add(ErrorObservation(
            error_type="DEPENDENCY_CONFLICT_ERROR",
            message=msg,
            raw_text=m.group(0),
        ))

    # 5f. Gradle build-script / plugin API failures
    for m in _BUILD_SCRIPT_ERROR.finditer(combined):
        plugin = (
            m.group("plugin") or m.group("plugin2") or m.group("plugin3") or ""
        ).strip()
        script_msg = (m.group("msg") or "").strip()
        if plugin:
            msg = f"Build-script error applying plugin [{plugin}]"
        elif script_msg:
            msg = f"Script compilation error: {script_msg}"
        else:
            msg = "Build-script error"
        add(ErrorObservation(
            error_type="BUILD_SCRIPT_ERROR",
            message=msg,
            raw_text=m.group(0),
        ))

    # 5g. API break — upgrade COMPILE_ERRORs that contain "Unresolved reference"
    #     or "Type mismatch" to API_BREAK_ERROR and extract the symbol name.
    #     These are re-classified from earlier COMPILE_ERROR matches if present,
    #     or added fresh if the COMPILE_ERROR patterns didn't match (e.g. a bare
    #     "Unresolved reference: X" message without file/line context).
    for m in _UNRESOLVED_REF.finditer(combined):
        symbol = m.group("symbol").strip()
        # Find the surrounding context (up to 200 chars before/after)
        start = max(0, m.start() - 100)
        end = min(len(combined), m.end() + 100)
        raw_ctx = combined[start:end].replace("\n", " ").strip()
        key = f"API_BREAK_ERROR|{symbol}"
        if key not in seen:
            seen.add(key)
            observations.append(ErrorObservation(
                error_type="API_BREAK_ERROR",
                message=f"Unresolved reference: {symbol}",
                raw_text=raw_ctx[:200],
                parser=parser_label,
                symbol_name=symbol,
            ))

    for m in _TYPE_MISMATCH.finditer(combined):
        details = m.group("details").strip()
        key = f"API_BREAK_ERROR|type_mismatch|{details[:50]}"
        if key not in seen:
            seen.add(key)
            observations.append(ErrorObservation(
                error_type="API_BREAK_ERROR",
                message=f"Type mismatch: {details}",
                raw_text=m.group(0),
                parser=parser_label,
            ))

    # 6. Kotlin errors without file location (only if < MAX_ERRORS so far)
    #    Skip messages already captured by KLIB_ABI pattern above.
    if len(observations) < _MAX_ERRORS:
        for m in _KT_NO_FILE.finditer(combined):
            msg = m.group("msg").strip()
            # Skip if already captured via file-level or KLIB pattern
            if any(o.message == msg or msg in o.message for o in observations):
                continue
            add(ErrorObservation(
                error_type="COMPILE_ERROR",
                message=msg,
                raw_text=m.group(0),
            ))

    # 6. Gradle "* What went wrong:" section — catches JVM/init failures such as
    #    Java version incompatibilities (e.g. Java 25 vs Kotlin parser expecting
    #    a two-component version string).  Only add if no other errors captured,
    #    to avoid duplicating messages already matched above.
    if not observations:
        for m in _GRADLE_WHAT_WENT_WRONG.finditer(combined):
            msg = m.group("msg").strip()
            if msg:
                add(ErrorObservation(
                    error_type="GRADLE_INIT_ERROR",
                    message=msg,
                    raw_text=m.group(0),
                ))

        # 7. JVM exception lines — fallback for env failures not covered above
        if not observations:
            for m in _JVM_EXCEPTION.finditer(combined):
                exc = m.group("exc")
                msg = m.group("msg").strip()
                add(ErrorObservation(
                    error_type="GRADLE_INIT_ERROR",
                    message=f"{exc}: {msg}",
                    raw_text=m.group(0),
                ))

    return observations[:_MAX_ERRORS]


def determine_status_from_output(
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str:
    """Map exit code + output to a `ValidationStatus` string."""
    from ..domain.validation import ValidationStatus

    if exit_code == 0:
        # Check if any test failures appear in the output
        if _has_test_failures(stdout + stderr):
            return ValidationStatus.FAILED_TESTS.value
        return ValidationStatus.SUCCESS_REPOSITORY_LEVEL.value

    combined = stdout + stderr
    if _GRADLE_RESOLVE.search(combined) or _GRADLE_NOT_FOUND.search(combined):
        return ValidationStatus.FAILED_BUILD.value
    if _KT_FILE_LINE.search(combined) or _KT_FILE_LINE_SIMPLE.search(combined):
        return ValidationStatus.FAILED_BUILD.value
    if _GRADLE_WHAT_WENT_WRONG.search(combined) or _JVM_EXCEPTION.search(combined):
        return ValidationStatus.FAILED_BUILD.value

    return ValidationStatus.FAILED_BUILD.value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_path(raw: str) -> str:
    """Strip leading path noise like '/home/user/project/' to a relative form."""
    p = raw.strip()
    # Keep paths relative when possible
    for prefix in ("file://", "file:"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


def _has_test_failures(text: str) -> bool:
    return (
        "tests failed" in text.lower()
        or "test failures" in text.lower()
        or ("FAILED" in text and "tests" in text.lower())
        or re.search(r"\d+ test(s)? failed", text, re.IGNORECASE) is not None
    )
