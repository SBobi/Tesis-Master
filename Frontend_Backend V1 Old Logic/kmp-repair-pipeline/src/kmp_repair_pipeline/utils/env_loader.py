"""Small .env loader used by CLI and web entrypoints.

This keeps local runtime configuration deterministic without requiring the
caller to manually export environment variables before each command.
"""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _candidate_env_files() -> list[Path]:
    cwd_env = Path.cwd() / ".env"
    repo_env = _repo_root() / ".env"

    # Preserve order and remove duplicates while keeping deterministic lookup.
    seen: set[Path] = set()
    candidates: list[Path] = []
    for path in (cwd_env, repo_env):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates.append(resolved)
    return candidates


def _normalize_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    # Support inline comments in plain assignments.
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def load_project_env(*, override_existing: bool = False) -> None:
    """Load .env values from cwd/repo root into process environment.

    Existing environment variables are preserved by default so shell-level
    overrides still win.
    """

    for env_path in _candidate_env_files():
        if not env_path.exists() or not env_path.is_file():
            continue

        for line in env_path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue

            if raw.startswith("export "):
                raw = raw[len("export ") :].strip()

            if "=" not in raw:
                continue

            key, raw_value = raw.split("=", 1)
            key = key.strip()
            if not key:
                continue

            if not override_existing and key in os.environ:
                continue

            value = _normalize_value(raw_value)
            if key == "GOOGLE_APPLICATION_CREDENTIALS" and value:
                creds_path = Path(value)
                if not creds_path.is_absolute():
                    value = str((env_path.parent / creds_path).resolve())

            os.environ[key] = value
