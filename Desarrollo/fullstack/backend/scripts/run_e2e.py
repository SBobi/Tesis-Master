#!/usr/bin/env python3
"""Python launcher for scripts/run_e2e.sh.

Examples:
    python scripts/run_e2e.py
    python scripts/run_e2e.py 3407b237-981f-40da-9623-4c4ac3c2087b
    python scripts/run_e2e.py -- --start-from localize --mode raw_error
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_BACKEND_E2E_SH = _HERE / "run_e2e.sh"
_WORKSPACE_ROOT = _HERE.parent.parent.parent


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
    parser = argparse.ArgumentParser(
        description="Run backend E2E shell script from Python. Unknown args are forwarded to run_e2e.sh."
    )
    parser.add_argument(
        "--no-homebrew-path",
        action="store_true",
        help="Do not prepend Homebrew Python locations to PATH before launching the shell script.",
    )
    args, forwarded = parser.parse_known_args(argv)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    if not _BACKEND_E2E_SH.exists():
        print(f"ERROR: script not found: {_BACKEND_E2E_SH}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    if not args.no_homebrew_path:
        _prepend_homebrew_path(env)

    cmd = ["bash", str(_BACKEND_E2E_SH), *forwarded]
    completed = subprocess.run(cmd, cwd=str(_WORKSPACE_ROOT), env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
