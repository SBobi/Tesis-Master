"""Consolidation domain types — merging static and dynamic evidence."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .analysis import ImpactGraph, ImpactRelation, SourceMetrics
from .validation import UIRegressions


class ScreenMapping(BaseModel):
    screen_name: str
    mapped_files: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    method: str = ""


class TraceEntry(BaseModel):
    file_path: str
    relation: ImpactRelation
    distance: int = 0
    screens: list[str] = Field(default_factory=list)
    metrics: SourceMetrics = Field(default_factory=SourceMetrics)


class ConsolidatedResult(BaseModel):
    dependency_group: str
    version_before: str
    version_after: str
    static_impact: ImpactGraph
    dynamic_regressions: UIRegressions
    screen_mappings: list[ScreenMapping] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)
    impacted_screens: list[str] = Field(default_factory=list)
    total_impacted_files: int = 0
    total_impacted_screens: int = 0
