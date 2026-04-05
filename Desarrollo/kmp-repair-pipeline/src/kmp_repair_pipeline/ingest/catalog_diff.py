"""Structured diff between two Gradle libs.versions.toml version catalogs.

Produces a ``CatalogDiff`` that surfaces:
  - alias renames   — an alias present in *before* is absent in *after* while
                      a new alias in *after* resolves to the same artifact module
  - artifact renames — the artifact module (group:name) changed for the same alias
  - added aliases   — new aliases in *after* that have no counterpart in *before*
  - removed aliases — aliases removed in *after*

This information is propagated into the repair context so the RepairAgent can
identify and fix version-catalog alias renames and artifact module changes that
are otherwise invisible (they appear as "Unresolved reference" COMPILE_ERRORs
but the agent cannot tell whether the fix is a catalog edit or a source edit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from .toml_parser import VersionCatalog


@dataclass
class AliasRename:
    """A library alias was renamed between before and after catalogs."""
    before_alias: str
    after_alias: str
    module: str              # the shared artifact module (e.g. "io.ktor:ktor-client-core")


@dataclass
class ArtifactRename:
    """The artifact module changed for the same alias."""
    alias: str
    before_module: str
    after_module: str


@dataclass
class CatalogDiff:
    """Structured diff between two version catalogs."""
    alias_renames: list[AliasRename] = field(default_factory=list)
    artifact_renames: list[ArtifactRename] = field(default_factory=list)
    added_aliases: list[str] = field(default_factory=list)
    removed_aliases: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.alias_renames
            or self.artifact_renames
            or self.added_aliases
            or self.removed_aliases
        )

    def to_dict(self) -> dict:
        return {
            "alias_renames": [
                {
                    "before_alias": r.before_alias,
                    "after_alias": r.after_alias,
                    "module": r.module,
                }
                for r in self.alias_renames
            ],
            "artifact_renames": [
                {
                    "alias": r.alias,
                    "before_module": r.before_module,
                    "after_module": r.after_module,
                }
                for r in self.artifact_renames
            ],
            "added_aliases": self.added_aliases,
            "removed_aliases": self.removed_aliases,
        }


def diff_catalogs(
    before: Union[VersionCatalog, str, Path],
    after: Union[VersionCatalog, str, Path],
) -> CatalogDiff:
    """Compare two version catalogs and return a structured ``CatalogDiff``.

    Parameters
    ----------
    before, after:
        Either a ``VersionCatalog`` instance, a filesystem path, or raw TOML
        string content.
    """
    before_cat = _to_catalog(before)
    after_cat = _to_catalog(after)

    before_libs = before_cat.libraries
    after_libs = after_cat.libraries

    before_aliases = set(before_libs)
    after_aliases = set(after_libs)

    kept_aliases = before_aliases & after_aliases
    removed_aliases = sorted(before_aliases - after_aliases)
    added_aliases = sorted(after_aliases - before_aliases)

    # Detect artifact renames for aliases present in both catalogs
    artifact_renames: list[ArtifactRename] = []
    for alias in sorted(kept_aliases):
        before_module = before_libs[alias].get("module", "")
        after_module = after_libs[alias].get("module", "")
        if before_module and after_module and before_module != after_module:
            artifact_renames.append(
                ArtifactRename(
                    alias=alias,
                    before_module=before_module,
                    after_module=after_module,
                )
            )

    # Detect alias renames: a removed alias whose module appears as an added alias
    # Example: "ktor-xml" removed, "ktor-client-content-negotiation-xmlutil" added,
    # both mapping to the same group.  We match on module string.
    after_module_to_alias: dict[str, str] = {
        info.get("module", ""): alias
        for alias, info in after_libs.items()
        if alias in added_aliases and info.get("module")
    }
    alias_renames: list[AliasRename] = []
    still_removed: list[str] = []
    still_added = set(added_aliases)

    for rem_alias in removed_aliases:
        module = before_libs[rem_alias].get("module", "")
        if module and module in after_module_to_alias:
            new_alias = after_module_to_alias[module]
            alias_renames.append(
                AliasRename(
                    before_alias=rem_alias,
                    after_alias=new_alias,
                    module=module,
                )
            )
            still_added.discard(new_alias)
        else:
            still_removed.append(rem_alias)

    return CatalogDiff(
        alias_renames=alias_renames,
        artifact_renames=artifact_renames,
        added_aliases=sorted(still_added),
        removed_aliases=still_removed,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_catalog(source: Union[VersionCatalog, str, Path]) -> VersionCatalog:
    if isinstance(source, VersionCatalog):
        return source
    # Delegate to the existing helper in version_catalog.py
    from .version_catalog import _catalog_from_source

    cat, tmp = _catalog_from_source(source)
    if tmp is not None:
        tmp.unlink(missing_ok=True)
    return cat
