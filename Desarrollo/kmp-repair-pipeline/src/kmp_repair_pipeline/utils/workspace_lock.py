"""File-based exclusive lock for a KMP repository workspace.

Prevents concurrent repair/validate invocations from corrupting the same
git workspace. Uses ``fcntl.flock`` (available on macOS and Linux).

Usage::

    from kmp_repair_pipeline.utils.workspace_lock import WorkspaceLock, WorkspaceLockError

    with WorkspaceLock(workspace_path):
        # only one process at a time runs here
        apply_patch(...)

The lock file is placed at ``<workspace>/.kmp-repair.lock``.  It is NOT
committed to git (gitignored by default as a dot-file starting with '.').
The lock is advisory (cooperative) — external tools that do not use this
class can still modify the workspace.  That is acceptable because the
pipeline itself is the only actor expected to touch the workspace.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from .log import get_logger

log = get_logger(__name__)

_LOCK_FILENAME = ".kmp-repair.lock"


class WorkspaceLockError(RuntimeError):
    """Raised when the workspace lock cannot be acquired within the timeout."""


class WorkspaceLock:
    """Exclusive file lock for a single KMP repository workspace directory.

    Parameters
    ----------
    workspace:
        Path to the repository clone (e.g. the ``after`` revision directory).
    timeout_s:
        How long to wait (in seconds) for the lock before raising
        ``WorkspaceLockError``.  Default 30 s — generous for normal use;
        large enough to survive a slow preceding test run.
    """

    def __init__(self, workspace: Path | str, timeout_s: float = 30.0) -> None:
        self.workspace = Path(workspace)
        self.timeout_s = timeout_s
        self.lock_file = self.workspace / _LOCK_FILENAME
        self._fh = None  # open file handle; kept alive while lock is held

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "WorkspaceLock":
        self.acquire()
        return self

    def __exit__(self, *_args) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Explicit acquire / release
    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Acquire the exclusive lock, blocking up to ``timeout_s`` seconds."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        # Open (or create) the lock file.  We keep the handle open until
        # release() so the OS keeps the flock active.
        fh = open(self.lock_file, "w", encoding="utf-8")  # noqa: WPS515
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Write our PID for debugging
                fh.write(str(os.getpid()))
                fh.flush()
                self._fh = fh
                log.debug("Workspace lock acquired: %s (pid=%d)", self.lock_file, os.getpid())
                return
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise WorkspaceLockError(
                        f"Cannot acquire workspace lock at {self.lock_file} "
                        f"after {self.timeout_s:.0f}s — another process may be "
                        f"running repair or validate on this workspace.  "
                        f"If no other process is running, delete the lock file and retry."
                    )
                time.sleep(0.25)

    def release(self) -> None:
        """Release the exclusive lock."""
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
                log.debug("Workspace lock released: %s", self.lock_file)
            except OSError:
                pass
            finally:
                self._fh = None
