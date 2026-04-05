"""Detect dependency version changes between two Gradle version catalogs.

Ported from prototype github_version_change.py with package rename and
domain model alignment.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .toml_parser import VersionCatalog
from ..domain.events import VersionChange


class VersionChangeSet(BaseModel):
    changes: list[VersionChange] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def detect_version_changes(before_toml: str | Path, after_toml: str | Path) -> VersionChangeSet:
    """Compare two version catalogs and return changed dependency groups.

    Keyed by ``version.ref`` so one version bump fans out to all dependency
    groups sharing the same alias.
    """
    before = VersionCatalog(Path(before_toml))
    after = VersionCatalog(Path(after_toml))

    groups_by_version_key: dict[str, set[str]] = {}
    for catalog in (before, after):
        for library in catalog.libraries.values():
            groups_by_version_key.setdefault(library["version_ref"], set()).add(library["group"])
        for plugin in catalog.plugins.values():
            groups_by_version_key.setdefault(plugin["version_ref"], set()).add(plugin["id"])

    changes: list[VersionChange] = []
    for version_key in sorted(groups_by_version_key):
        before_version = before.get_version(version_key)
        after_version = after.get_version(version_key)
        if before_version is None or after_version is None or before_version == after_version:
            continue
        for dependency_group in sorted(groups_by_version_key[version_key]):
            changes.append(
                VersionChange(
                    dependency_group=dependency_group,
                    version_key=version_key,
                    before=before_version,
                    after=after_version,
                )
            )

    return VersionChangeSet(changes=changes)
