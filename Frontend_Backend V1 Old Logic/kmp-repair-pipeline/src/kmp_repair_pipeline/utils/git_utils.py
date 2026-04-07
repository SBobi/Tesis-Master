"""Git helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .log import get_logger

log = get_logger(__name__)


def clone_repo(url: str, dest: Path, depth: int = 1) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", url, str(dest)]
    if depth > 0:
        cmd = ["git", "clone", f"--depth={depth}", url, str(dest)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    log.info(f"Cloned {url} → {dest}")
    return dest


def is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def get_head_sha(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def checkout(repo_path: Path, ref: str) -> None:
    subprocess.run(
        ["git", "checkout", ref],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    log.info(f"Checked out {ref} in {repo_path}")
