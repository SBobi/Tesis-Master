"""Phase 13 — Orchestrate report generation and write output files.

Produces CSV, JSON, and/or Markdown reports from evaluation_metrics rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..utils.json_io import sha256_of_file
from ..utils.log import get_logger
from .formatters import aggregate_by_mode, to_csv, to_json, to_markdown
from .report_builder import ReportRow, build_report

log = get_logger(__name__)

FORMATS = ("csv", "json", "markdown", "all")


@dataclass
class ReportResult:
    output_dir: str
    row_count: int
    files: list[str] = field(default_factory=list)
    aggregates: dict = field(default_factory=dict)


def generate_report(
    session: Session,
    output_dir: Path | str,
    formats: tuple[str, ...] = ("all",),
    repair_modes: Optional[list[str]] = None,
    case_ids: Optional[list[str]] = None,
) -> ReportResult:
    """Build and write evaluation reports.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.
    output_dir:
        Directory to write report files.  Created if it does not exist.
    formats:
        Which formats to write.  ``"all"`` writes csv + json + markdown.
    repair_modes:
        If given, restrict to these repair modes.
    case_ids:
        If given, restrict to these case UUIDs.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows: list[ReportRow] = build_report(
        session=session,
        repair_modes=repair_modes,
        case_ids=case_ids,
    )

    emit_all = "all" in formats
    written: list[str] = []

    if emit_all or "csv" in formats:
        path = out / "report.csv"
        path.write_text(to_csv(rows), encoding="utf-8")
        written.append(str(path))
        log.info("Wrote CSV report: %s", path)

    if emit_all or "json" in formats:
        path = out / "report.json"
        path.write_text(to_json(rows), encoding="utf-8")
        written.append(str(path))
        log.info("Wrote JSON report: %s", path)

    if emit_all or "markdown" in formats:
        path = out / "report.md"
        path.write_text(to_markdown(rows), encoding="utf-8")
        written.append(str(path))
        log.info("Wrote Markdown report: %s", path)

    agg = aggregate_by_mode(rows)
    log.info(
        "Report complete: %d row(s), %d file(s), modes=%s",
        len(rows), len(written), list(agg.keys()),
    )

    return ReportResult(
        output_dir=str(out),
        row_count=len(rows),
        files=written,
        aggregates=agg,
    )
