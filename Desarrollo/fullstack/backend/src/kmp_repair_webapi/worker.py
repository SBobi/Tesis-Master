"""RQ worker entrypoint for kmp-repair web jobs."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Callable

from rq import Connection, Worker

from .env_loader import load_project_env
from .queue import get_redis_connection
from .settings import get_settings

# Load .env early so worker jobs inherit project runtime configuration.
load_project_env()


# Use kmp_repair_pipeline logger if available, else stdlib logging
try:
    from kmp_repair_pipeline.utils.log import get_logger
    log = get_logger(__name__)
except ImportError:
    import logging
    log = logging.getLogger(__name__)


def _resolve_java_21_home() -> str | None:
    """Return JAVA_HOME for JDK 21 on macOS, or None when unavailable."""
    try:
        result = subprocess.run(
            ["/usr/libexec/java_home", "-v", "21"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    home = (result.stdout or "").strip()
    if not home:
        return None
    if not Path(home).exists():
        return None
    return home


def _prepend_path(path_entry: str) -> None:
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    if parts and parts[0] == path_entry:
        return

    filtered = [p for p in parts if p != path_entry]
    os.environ["PATH"] = os.pathsep.join([path_entry, *filtered]) if filtered else path_entry


def configure_worker_runtime(
    *,
    system_name: str | None = None,
    java_21_home_resolver: Callable[[], str | None] | None = None,
) -> None:
    """Apply safe defaults so the worker runs reliably on macOS."""
    system_name = system_name or platform.system()
    if system_name != "Darwin":
        return

    if "OBJC_DISABLE_INITIALIZE_FORK_SAFETY" not in os.environ:
        os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
        log.info("Set OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES for macOS worker")

    resolver = java_21_home_resolver or _resolve_java_21_home
    java_home = resolver()
    if java_home:
        os.environ["JAVA_HOME"] = java_home
        _prepend_path(str(Path(java_home) / "bin"))
        log.info("Using JAVA_HOME=%s", java_home)
    else:
        log.warning("JDK 21 not found via /usr/libexec/java_home -v 21; keeping current Java configuration")


def _bootstrap_runtime_if_needed(
    *,
    system_name: str | None = None,
    java_21_home_resolver: Callable[[], str | None] | None = None,
) -> bool:
    system_name = system_name or platform.system()
    if system_name != "Darwin":
        return False

    if os.environ.get("KMP_WORKER_BOOTSTRAPPED") == "1":
        configure_worker_runtime(system_name=system_name, java_21_home_resolver=java_21_home_resolver)
        return False

    before_objc = os.environ.get("OBJC_DISABLE_INITIALIZE_FORK_SAFETY")
    before_java = os.environ.get("JAVA_HOME")
    before_path = os.environ.get("PATH", "")

    configure_worker_runtime(system_name=system_name, java_21_home_resolver=java_21_home_resolver)

    changed = (
        os.environ.get("OBJC_DISABLE_INITIALIZE_FORK_SAFETY") != before_objc
        or os.environ.get("JAVA_HOME") != before_java
        or os.environ.get("PATH", "") != before_path
    )
    if not changed:
        return False

    os.environ["KMP_WORKER_BOOTSTRAPPED"] = "1"
    return True


def run() -> None:
    if _bootstrap_runtime_if_needed():
        os.execvpe(sys.executable, [sys.executable, *sys.argv], os.environ)

    settings = get_settings()
    connection = get_redis_connection()
    with Connection(connection):
        worker = Worker([settings.queue_name])
        worker.work(with_scheduler=False)
