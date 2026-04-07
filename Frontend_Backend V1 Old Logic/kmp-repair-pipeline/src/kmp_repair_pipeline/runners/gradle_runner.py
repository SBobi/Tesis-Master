"""Execute Gradle tasks via `./gradlew` and capture structured output.

Design constraints (from thesis):
  - Reproducible: same inputs → same outputs. Uses `--no-daemon` to avoid
    state leaking between before/after runs.
  - Honest: never claims success if the process returned non-zero.
  - Bounded: enforces a timeout (default 10 min) so one stuck build
    cannot block the pipeline indefinitely.
  - Artifact-preserving: stdout/stderr are written to the ArtifactStore
    and SHA-256 hashed before being stored in the DB.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..case_bundle.evidence import ErrorObservation, TaskOutcome
from ..runners.error_parser import determine_status_from_output, parse
from ..utils.log import get_logger

log = get_logger(__name__)

# Default Gradle task sequence for KMP compilation check
# :compileCommonMainKotlinMetadata → shared Kotlin
# :assembleDebug                   → Android debug APK
# These are overridden per target by execution_runner
DEFAULT_TASKS: dict[str, list[str]] = {
    "shared": [
        "compileCommonMainKotlinMetadata",
        "compileKotlinJvm",
    ],
    "android": [
        "assembleDebug",
        "testDebugUnitTest",
    ],
    "ios": [
        # KMP framework compilation (no Xcode required, just Kotlin)
        "compileKotlinIosArm64",
        "compileKotlinIosSimulatorArm64",
        "compileKotlinIosX64",
    ],
    "all": [
        "build",
    ],
}

# Gradle flags applied to every invocation
_GRADLE_FLAGS = [
    "--no-daemon",
    "--stacktrace",
    "--continue",       # keep going after first failure (collect all errors)
]


@dataclass
class GradleRunResult:
    """Raw result of one `./gradlew` invocation."""
    task_name: str           # logical name (e.g. "compileCommonMainKotlinMetadata")
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    started_at: datetime
    ended_at: datetime
    error_observations: list[ErrorObservation]
    status: str              # ValidationStatus value


def run_tasks(
    repo_path: Path | str,
    tasks: list[str],
    env_extra: Optional[dict[str, str]] = None,
    timeout_s: int = 600,
    gradle_flags: Optional[list[str]] = None,
) -> list[GradleRunResult]:
    """Run one or more Gradle tasks in `repo_path`.

    Each task name is run as a separate Gradle invocation so we get
    per-task exit codes and output. Returns results in input order.

    Parameters
    ----------
    repo_path:
        Directory containing `gradlew`.
    tasks:
        Gradle task names (without leading `:` — added automatically).
    env_extra:
        Extra environment variables merged into the current process env.
    timeout_s:
        Maximum seconds per task invocation.
    gradle_flags:
        Override the default `--no-daemon --stacktrace --continue` flags.
    """
    repo = Path(repo_path)
    gradlew = repo / "gradlew"

    if not gradlew.exists():
        raise FileNotFoundError(f"gradlew not found in {repo}")

    if not gradlew.stat().st_mode & 0o111:
        gradlew.chmod(0o755)

    flags = gradle_flags if gradle_flags is not None else _GRADLE_FLAGS
    import os
    run_env = {**os.environ, **(env_extra or {})}

    results: list[GradleRunResult] = []
    for task in tasks:
        result = _run_single_task(repo, gradlew, task, flags, run_env, timeout_s)
        results.append(result)

    return results


def _run_single_task(
    repo: Path,
    gradlew: Path,
    task: str,
    flags: list[str],
    env: dict,
    timeout_s: int,
) -> GradleRunResult:
    cmd = [str(gradlew)] + flags + [task]
    log.info("Running: %s (cwd=%s)", " ".join(cmd), repo)

    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo),
            env=env,
            timeout=timeout_s,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        log.error("Task %s timed out after %ds", task, timeout_s)
        stdout = exc.stdout.decode() if exc.stdout else ""
        stderr = (exc.stderr.decode() if exc.stderr else "") + f"\n[TIMEOUT after {timeout_s}s]"
        exit_code = -1
    except Exception as exc:
        log.error("Failed to launch gradlew for task %s: %s", task, exc)
        stdout = ""
        stderr = str(exc)
        exit_code = -2

    ended_at = datetime.now(timezone.utc)
    duration_s = time.monotonic() - t0

    errors = parse(stdout, stderr)
    status = determine_status_from_output(exit_code, stdout, stderr)

    log.info(
        "Task %s finished: exit_code=%d status=%s duration=%.1fs errors=%d",
        task, exit_code, status, duration_s, len(errors),
    )

    return GradleRunResult(
        task_name=task,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration_s,
        started_at=started_at,
        ended_at=ended_at,
        error_observations=errors,
        status=status,
    )


def tasks_for_target(target: str) -> list[str]:
    """Return the default Gradle task list for a given KMP target."""
    return DEFAULT_TASKS.get(target, DEFAULT_TASKS["all"])
