"""Small .env loader for web entrypoints.

Adapted from the V1 web layer. The canonical pipeline does not expose this
utility, so it is bundled here to avoid modifying kmp-repair-pipeline.
"""

from __future__ import annotations

import os
from pathlib import Path


def _candidate_env_files() -> list[Path]:
    here = Path(__file__).resolve()

    # fullstack/backend/src/kmp_repair_webapi/env_loader.py
    #                           ^ parent[2] => fullstack/backend
    #                           ^ parent[4] => workspace root (Desarrollo)
    try:
        backend_root = here.parents[2]
    except IndexError:
        backend_root = Path.cwd()

    try:
        workspace_root = here.parents[4]
    except IndexError:
        workspace_root = backend_root.parent

    backend_env = backend_root / ".env"
    cwd_env = Path.cwd() / ".env"
    workspace_env = workspace_root / ".env"
    pipeline_env = workspace_root / "kmp-repair-pipeline" / ".env"

    seen: set[Path] = set()
    candidates: list[Path] = []
    for path in (backend_env, cwd_env, workspace_env, pipeline_env):
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

    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def load_project_env(*, override_existing: bool = False) -> None:
    """Load .env values from cwd / backend root into process environment."""
    for env_path in _candidate_env_files():
        if not env_path.exists() or not env_path.is_file():
            continue

        for line in env_path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue

            if raw.startswith("export "):
                raw = raw[len("export "):].strip()

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
