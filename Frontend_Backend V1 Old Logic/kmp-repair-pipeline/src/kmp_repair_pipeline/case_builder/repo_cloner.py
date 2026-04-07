"""Clone a git repository at a specific ref into a local directory.

Provides reproducible before/after workspace copies for execution.
Uses `git clone --depth=1` for speed, then `git checkout <sha>` to pin.

All paths returned are absolute. Callers decide the cleanup strategy.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..utils.log import get_logger

log = get_logger(__name__)


class ClonerError(Exception):
    """Raised when git operations fail."""


def clone_at_ref(
    repo_url: str,
    ref: str,
    target_dir: Path | str,
    *,
    overwrite: bool = False,
) -> Path:
    """Clone `repo_url` and check out `ref` (SHA or branch) into `target_dir`.

    Parameters
    ----------
    repo_url:
        HTTPS URL of the repository (e.g. ``https://github.com/owner/repo``).
    ref:
        Git commit SHA or branch name to check out.
    target_dir:
        Directory where the clone will be created. Must not exist unless
        ``overwrite=True``.
    overwrite:
        If True and `target_dir` already contains files, wipe it first.

    Returns
    -------
    Path
        Absolute path to the cloned repository.
    """
    dest = Path(target_dir).resolve()

    if dest.exists():
        if overwrite:
            log.info("Removing existing directory: %s", dest)
            shutil.rmtree(dest)
        else:
            log.info("Reusing existing clone at %s", dest)
            return dest

    dest.mkdir(parents=True, exist_ok=True)

    # Shallow clone of the default branch, then fetch the exact ref
    log.info("Cloning %s → %s (ref: %s)", repo_url, dest, ref[:12] if len(ref) > 12 else ref)

    _run(["git", "clone", "--depth=50", "--no-single-branch", repo_url, str(dest)])

    # Pin to the exact commit
    _run(["git", "-C", str(dest), "checkout", ref])

    log.info("Clone ready: %s", dest)
    return dest


def clone_before_after(
    repo_url: str,
    base_sha: str,
    head_sha: str,
    work_dir: Path | str,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Clone two revisions (base, head) of `repo_url` into `work_dir`.

    Returns
    -------
    (before_path, after_path)
        Absolute paths to the before and after clones.
    """
    work = Path(work_dir).resolve()
    before = clone_at_ref(repo_url, base_sha, work / "before", overwrite=overwrite)
    after = clone_at_ref(repo_url, head_sha, work / "after", overwrite=overwrite)
    return before, after


def is_git_repo(path: Path | str) -> bool:
    """Return True if `path` is the root of a git repository."""
    return (Path(path) / ".git").exists()


def local_head_sha(path: Path | str) -> str:
    """Return the current HEAD SHA of a local git repo."""
    result = _run(["git", "-C", str(path), "rev-parse", "HEAD"], capture=True)
    return result.strip()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _run(cmd: list[str], capture: bool = False) -> str:
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ClonerError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n{stderr}"
        )
    return result.stdout if capture else ""
