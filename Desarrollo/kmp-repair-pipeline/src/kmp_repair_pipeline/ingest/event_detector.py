"""Detect dependency update events from a repository.

Stub — full implementation in Phase 4.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.events import DependencyUpdateEvent, UpdateClass
from ..utils.log import get_logger
from .event_classifier import classify_update
from .version_catalog import detect_version_changes

log = get_logger(__name__)


def detect_events_from_toml_diff(
    repo_local_path: str,
    before_toml: str | Path,
    after_toml: str | Path,
    repo_url: str = "",
    pr_ref: str | None = None,
) -> list[DependencyUpdateEvent]:
    """Produce DependencyUpdateEvents from a before/after version catalog diff."""
    change_set = detect_version_changes(before_toml, after_toml)
    if not change_set.has_changes:
        log.info("No version changes detected between the two catalogs.")
        return []

    events: list[DependencyUpdateEvent] = []
    for change in change_set.changes:
        update_class = classify_update(change, build_file_paths=[])
        event = DependencyUpdateEvent(
            repo_url=repo_url,
            repo_local_path=repo_local_path,
            pr_ref=pr_ref,
            version_changes=[change],
            update_class=update_class,
        )
        events.append(event)
        log.info(
            f"Detected [{update_class.value}] {change.dependency_group}: "
            f"{change.before} → {change.after}"
        )

    return events
