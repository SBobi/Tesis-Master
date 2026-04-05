"""Assemble ReportRow objects from the DB for all evaluated cases.

No I/O — only DB reads via SQLAlchemy.  Returns a plain list of
ReportRow dataclasses that formatters can render into any format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..storage.models import EvaluationMetric, RepairCase
from ..storage.repositories import EvaluationMetricRepo, RepairCaseRepo
from ..utils.log import get_logger

log = get_logger(__name__)


@dataclass
class ReportRow:
    """One row in the evaluation report — one (case, repair_mode) pair."""
    case_id: str
    repair_mode: str
    case_status: str
    repo_url: str
    pr_ref: str
    update_class: str
    bsr: Optional[float]
    ctsr: Optional[float]
    ffsr: Optional[float]
    efr: Optional[float]
    hit_at_1: Optional[float]
    hit_at_3: Optional[float]
    hit_at_5: Optional[float]
    source_set_accuracy: Optional[float]
    extra: dict = field(default_factory=dict)


def build_report(
    session: Session,
    repair_modes: Optional[list[str]] = None,
    case_ids: Optional[list[str]] = None,
) -> list[ReportRow]:
    """Query the DB and return one ReportRow per (case, repair_mode) pair.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.
    repair_modes:
        If given, restrict to these repair modes only.
    case_ids:
        If given, restrict to these case UUIDs only.
    """
    metric_repo = EvaluationMetricRepo(session)

    if case_ids:
        metrics: list[EvaluationMetric] = []
        for cid in case_ids:
            rows = metric_repo.list_for_case(cid)
            if repair_modes:
                rows = [r for r in rows if r.repair_mode in repair_modes]
            metrics.extend(rows)
    else:
        metrics = metric_repo.list_all(repair_modes=repair_modes)

    if not metrics:
        log.warning("No evaluation metrics found — run `kmp-repair metrics` first")
        return []

    # Build a case_id → RepairCase index (one DB look-up per unique case)
    unique_case_ids = {m.repair_case_id for m in metrics}
    case_repo = RepairCaseRepo(session)
    case_index: dict[str, RepairCase] = {
        cid: case_repo.get_by_id(cid)
        for cid in unique_case_ids
        if case_repo.get_by_id(cid) is not None
    }

    rows: list[ReportRow] = []
    for m in metrics:
        case_row = case_index.get(m.repair_case_id)
        repo_url, pr_ref, update_class = _extract_event_info(case_row)

        rows.append(ReportRow(
            case_id=m.repair_case_id,
            repair_mode=m.repair_mode,
            case_status=case_row.status if case_row else "UNKNOWN",
            repo_url=repo_url,
            pr_ref=pr_ref,
            update_class=update_class,
            bsr=m.bsr,
            ctsr=m.ctsr,
            ffsr=m.ffsr,
            efr=m.efr,
            hit_at_1=m.hit_at_1,
            hit_at_3=m.hit_at_3,
            hit_at_5=m.hit_at_5,
            source_set_accuracy=m.source_set_accuracy,
            extra=m.extra or {},
        ))

    log.info("Report assembled: %d row(s) across %d case(s)", len(rows), len(unique_case_ids))
    return rows


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_event_info(case_row: Optional[RepairCase]) -> tuple[str, str, str]:
    """Return (repo_url, pr_ref, update_class) from the joined event / repo."""
    if case_row is None:
        return "", "", ""
    try:
        event = case_row.dependency_event
        repo_url = event.repository.url if event and event.repository else ""
        pr_ref = event.pr_ref or "" if event else ""
        update_class = event.update_class if event else ""
        return repo_url, pr_ref, update_class
    except Exception:
        return "", "", ""
