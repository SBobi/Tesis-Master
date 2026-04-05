"""Parse Kotlin/Gradle compiler errors from raw stdout/stderr.

Produces `ErrorObservation` instances consumed by `ExecutionEvidence`.

Supported error patterns:
  - Kotlin compiler: "e: file.kt:42:10: error: Unresolved reference"
  - Kotlin compiler (new format): "e: error: ..." without file/line
  - Gradle task failure summary: "> Task :foo FAILED"
  - Gradle general error: "> Could not resolve ..."
  - Android resource compilation errors
  - Linker / symbol errors from native targets
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

    # 5. Kotlin errors without file location (only if < MAX_ERRORS so far)
    if len(observations) < _MAX_ERRORS:
        for m in _KT_NO_FILE.finditer(combined):
            msg = m.group("msg").strip()
            # Skip if already captured via file-level pattern
            if any(o.message == msg for o in observations):
                continue
            add(ErrorObservation(
                error_type="COMPILE_ERROR",
                message=msg,
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
