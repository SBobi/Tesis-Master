"""Dependency update event types — Stage 1 of the thesis pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UpdateClass(str, Enum):
    """Classification of a dependency update event.

    Based on the taxonomy in the thesis (informed by Jayasuriya et al.,
    He et al., Gromov & Chernyshev).
    """
    DIRECT_LIBRARY = "direct_library"
    PLUGIN_TOOLCHAIN = "plugin_toolchain"
    TRANSITIVE = "transitive"
    PLATFORM_INTEGRATION = "platform_integration"
    UNKNOWN = "unknown"


class VersionChange(BaseModel):
    """A single dependency version delta."""
    dependency_group: str
    version_key: str
    before: str
    after: str


class DependencyUpdateEvent(BaseModel):
    """A typed, ingested dependency update event — the entry point for one repair case.

    Produced by Stage 1 (update ingestion and typing).
    """
    repo_url: str
    repo_local_path: str = ""
    pr_ref: Optional[str] = None
    version_changes: list[VersionChange] = Field(default_factory=list)
    update_class: UpdateClass = UpdateClass.UNKNOWN
    raw_diff: str = ""
    build_file_paths: list[str] = Field(default_factory=list)
    # Auxiliary only — not the primary source of truth (see thesis §III.B)
    sbom_path: Optional[str] = None
    github_dep_graph_path: Optional[str] = None
