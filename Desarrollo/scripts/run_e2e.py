#!/usr/bin/env python3
"""Workspace-root Python launcher for scripts/run_e2e.sh."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE / "run_e2e.sh"


def _prepend_homebrew_path(env: dict[str, str]) -> None:
    prefixes = ["/opt/homebrew/opt/python@3.12/bin", "/opt/homebrew/bin"]
    current = env.get("PATH", "")
    parts = [p for p in current.split(":") if p]

    for prefix in reversed(prefixes):
        if prefix in parts:
            parts.remove(prefix)
        parts.insert(0, prefix)

    env["PATH"] = ":".join(parts)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not _SCRIPT.exists():
        print(f"ERROR: script not found: {_SCRIPT}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    _prepend_homebrew_path(env)

    cmd = ["bash", str(_SCRIPT), *argv]
    completed = subprocess.run(cmd, cwd=str(_HERE.parent), env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
