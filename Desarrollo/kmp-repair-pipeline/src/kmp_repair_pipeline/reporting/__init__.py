"""Phase 13 — Evaluation report generation (CSV, JSON, Markdown)."""

from .formatters import aggregate_by_mode, to_csv, to_json, to_markdown
from .report_builder import ReportRow, build_report
from .reporter import FORMATS, ReportResult, generate_report

__all__ = [
    "generate_report",
    "ReportResult",
    "FORMATS",
    "build_report",
    "ReportRow",
    "to_csv",
    "to_json",
    "to_markdown",
    "aggregate_by_mode",
]
