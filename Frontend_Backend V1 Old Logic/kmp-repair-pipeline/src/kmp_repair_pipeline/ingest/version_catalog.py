"""Detect dependency version changes between two Gradle version catalogs.

Ported from prototype github_version_change.py with package rename and
domain model alignment.
"""

from __future__ import annotations

import tempfile
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
    before, before_tmp = _catalog_from_source(before_toml)
    after, after_tmp = _catalog_from_source(after_toml)

    try:
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
    finally:
        for p in (before_tmp, after_tmp):
            if p is not None:
                p.unlink(missing_ok=True)


def _catalog_from_source(source: str | Path) -> tuple[VersionCatalog, Path | None]:
    """Build VersionCatalog from either a filesystem path or raw TOML content."""
    if isinstance(source, Path):
        return VersionCatalog(source), None

    if isinstance(source, str):
        # If string looks like a real existing path, keep file-based flow.
        if "\n" not in source and "\r" not in source:
            candidate = Path(source)
            if candidate.exists():
                return VersionCatalog(candidate), None

        # Otherwise treat string as raw TOML content.
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
        try:
            tmp.write(source)
        finally:
            tmp.close()
        tmp_path = Path(tmp.name)
        return VersionCatalog(tmp_path), tmp_path

    raise TypeError(f"Unsupported source type: {type(source)!r}")
