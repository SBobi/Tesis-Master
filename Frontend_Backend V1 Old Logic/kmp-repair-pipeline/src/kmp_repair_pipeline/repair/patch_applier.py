"""Apply a unified diff to a local repository clone.

Uses `patch --forward --batch` for robust application. Falls back to
`git apply` when `patch` is not available.

Returns a `PatchApplicationResult` that records:
  - Whether application succeeded
  - Which files were touched (from diff header)
  - Reject file paths if partial application occurred
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..utils.log import get_logger

log = get_logger(__name__)


@dataclass
class PatchApplicationResult:
    success: bool
    touched_files: list[str] = field(default_factory=list)
    rejected_files: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    method: str = "patch"   # "patch" | "git_apply" | "none"


def apply_patch(diff_text: str, repo_path: Path | str) -> PatchApplicationResult:
    """Apply `diff_text` (unified diff) to `repo_path`.

    Tries `patch` first; falls back to `git apply` if not available.
    Returns a result regardless of success — never raises on patch failure.
    """
    repo = Path(repo_path)
    if not diff_text.strip():
        return PatchApplicationResult(success=False, stderr="Empty diff")

    if shutil.which("patch"):
        return _apply_with_patch(diff_text, repo)
    elif shutil.which("git"):
        return _apply_with_git(diff_text, repo)
    else:
        log.error("Neither `patch` nor `git` found — cannot apply diff")
        return PatchApplicationResult(
            success=False,
            stderr="patch/git not available in PATH",
            method="none",
        )


def revert_patch(diff_text: str, repo_path: Path | str) -> PatchApplicationResult:
    """Revert a previously applied patch (reverse application)."""
    repo = Path(repo_path)
    if shutil.which("patch"):
        return _apply_with_patch(diff_text, repo, reverse=True)
    elif shutil.which("git"):
        return _apply_with_git(diff_text, repo, reverse=True)
    return PatchApplicationResult(success=False, stderr="patch/git not available", method="none")


def extract_touched_files(diff_text: str) -> list[str]:
    """Return file paths modified by this diff (from +++ lines)."""
    paths: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path and path != "/dev/null":
                paths.append(path)
    return list(dict.fromkeys(paths))


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _apply_with_patch(
    diff_text: str,
    repo: Path,
    reverse: bool = False,
) -> PatchApplicationResult:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(diff_text)
        diff_file = f.name

    cmd = ["patch", "--forward", "--batch", "-p1", "-i", diff_file]
    if reverse:
        cmd.append("--reverse")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo),
            timeout=60,
        )
        success = result.returncode == 0
        rejects = _find_reject_files(repo)
        if rejects:
            log.warning("Patch left reject files: %s", rejects)

        return PatchApplicationResult(
            success=success,
            touched_files=extract_touched_files(diff_text),
            rejected_files=rejects,
            stdout=result.stdout,
            stderr=result.stderr,
            method="patch",
        )
    except subprocess.TimeoutExpired:
        return PatchApplicationResult(
            success=False,
            stderr="patch command timed out",
            method="patch",
        )
    finally:
        Path(diff_file).unlink(missing_ok=True)


def _apply_with_git(
    diff_text: str,
    repo: Path,
    reverse: bool = False,
) -> PatchApplicationResult:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(diff_text)
        diff_file = f.name

    cmd = ["git", "apply", "--ignore-whitespace", diff_file]
    if reverse:
        cmd.append("--reverse")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo),
            timeout=60,
        )
        return PatchApplicationResult(
            success=result.returncode == 0,
            touched_files=extract_touched_files(diff_text),
            stdout=result.stdout,
            stderr=result.stderr,
            method="git_apply",
        )
    except subprocess.TimeoutExpired:
        return PatchApplicationResult(
            success=False,
            stderr="git apply timed out",
            method="git_apply",
        )
    finally:
        Path(diff_file).unlink(missing_ok=True)


def _find_reject_files(repo: Path) -> list[str]:
    return [
        str(p.relative_to(repo))
        for p in repo.rglob("*.rej")
    ]
