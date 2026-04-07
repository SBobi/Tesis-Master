"""Pure format renderers for evaluation reports.

No I/O — all functions receive a list[ReportRow] and return a string.
The caller is responsible for writing to disk.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Optional

from .report_builder import ReportRow

# Ordered column names — used by all three formats
_COLUMNS = [
    "case_id",
    "repair_mode",
    "case_status",
    "repo_url",
    "pr_ref",
    "update_class",
    "bsr",
    "ctsr",
    "ffsr",
    "efr",
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "source_set_accuracy",
]


def to_csv(rows: list[ReportRow]) -> str:
    """Render rows as a CSV string (header included)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(_row_to_dict(r))
    return buf.getvalue()


def to_json(rows: list[ReportRow]) -> str:
    """Render rows as a JSON array string."""
    return json.dumps([_row_to_dict(r) for r in rows], indent=2, default=str)


def to_markdown(rows: list[ReportRow]) -> str:
    """Render rows as a Markdown table."""
    if not rows:
        return "_No evaluation results found._\n"

    headers = _COLUMNS
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"

    data_lines = []
    for r in rows:
        d = _row_to_dict(r)
        cells = [_fmt_cell(d.get(col)) for col in headers]
        data_lines.append("| " + " | ".join(cells) + " |")

    lines = [
        "# KMP Repair Pipeline — Evaluation Report",
        "",
        header_line,
        sep_line,
        *data_lines,
        "",
    ]

    # Append aggregate summary
    lines += _summary_section(rows)
    lines += _attempt_strategy_section(rows)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Aggregate summary helpers
# ---------------------------------------------------------------------------


def aggregate_by_mode(rows: list[ReportRow]) -> dict[str, dict]:
    """Compute per-mode averages across all cases.

    Returns a dict mapping repair_mode → {metric: avg | None}.
    """
    from collections import defaultdict

    buckets: dict[str, list[ReportRow]] = defaultdict(list)
    for r in rows:
        buckets[r.repair_mode].append(r)

    result: dict[str, dict] = {}
    for mode, mode_rows in sorted(buckets.items()):
        result[mode] = {
            "n": len(mode_rows),
            "bsr": _mean([r.bsr for r in mode_rows]),
            "ctsr": _mean([r.ctsr for r in mode_rows]),
            "ffsr": _mean([r.ffsr for r in mode_rows]),
            "efr": _mean([r.efr for r in mode_rows if r.efr is not None]),
            "hit_at_1": _mean([r.hit_at_1 for r in mode_rows if r.hit_at_1 is not None]),
            "hit_at_3": _mean([r.hit_at_3 for r in mode_rows if r.hit_at_3 is not None]),
            "hit_at_5": _mean([r.hit_at_5 for r in mode_rows if r.hit_at_5 is not None]),
            "source_set_accuracy": _mean(
                [r.source_set_accuracy for r in mode_rows if r.source_set_accuracy is not None]
            ),
        }
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_dict(r: ReportRow) -> dict:
    return {
        "case_id": r.case_id,
        "repair_mode": r.repair_mode,
        "case_status": r.case_status,
        "repo_url": r.repo_url,
        "pr_ref": r.pr_ref,
        "update_class": r.update_class,
        "bsr": r.bsr,
        "ctsr": r.ctsr,
        "ffsr": r.ffsr,
        "efr": r.efr,
        "hit_at_1": r.hit_at_1,
        "hit_at_3": r.hit_at_3,
        "hit_at_5": r.hit_at_5,
        "source_set_accuracy": r.source_set_accuracy,
    }


def _fmt_cell(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _mean(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _summary_section(rows: list[ReportRow]) -> list[str]:
    agg = aggregate_by_mode(rows)
    if not agg:
        return []
    lines = ["## Per-Mode Averages", ""]
    metric_cols = ["bsr", "ctsr", "ffsr", "efr", "hit_at_1", "hit_at_3", "hit_at_5",
                   "source_set_accuracy"]
    h = "| mode | n | " + " | ".join(metric_cols) + " |"
    s = "| --- | --- | " + " | ".join("---" for _ in metric_cols) + " |"
    lines += [h, s]
    for mode, vals in agg.items():
        cells = [_fmt_cell(vals.get(m)) for m in metric_cols]
        lines.append(f"| {mode} | {vals['n']} | " + " | ".join(cells) + " |")
    return lines


def _attempt_strategy_section(rows: list[ReportRow]) -> list[str]:
    """Render per-attempt strategy comparison extracted from row.extra."""
    attempt_rows: list[dict] = []
    for r in rows:
        for a in (r.extra or {}).get("attempts", []):
            attempt_rows.append(
                {
                    "case_id": r.case_id,
                    "repair_mode": r.repair_mode,
                    "attempt_number": a.get("attempt_number", ""),
                    "patch_strategy": a.get("patch_strategy", "single_diff"),
                    "patch_status": a.get("patch_status", ""),
                    "validation_status": a.get("validation_status", "NOT_RUN"),
                    "created_at": a.get("created_at", ""),
                }
            )

    if not attempt_rows:
        return []

    # Deduplicate by case/mode/attempt in case sources overlap.
    unique = {}
    for row in attempt_rows:
        key = (row["case_id"], row["repair_mode"], row["attempt_number"])
        unique[key] = row
    flattened = list(unique.values())
    flattened.sort(key=lambda r: (r["case_id"], r["repair_mode"], r["attempt_number"]))

    lines = [
        "",
        "## Attempt Strategy Comparison",
        "",
        "| case_id | repair_mode | attempt_number | patch_strategy | patch_status | validation_status | created_at |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in flattened:
        lines.append(
            "| "
            f"{_fmt_cell(row['case_id'])} | "
            f"{_fmt_cell(row['repair_mode'])} | "
            f"{_fmt_cell(row['attempt_number'])} | "
            f"{_fmt_cell(row['patch_strategy'])} | "
            f"{_fmt_cell(row['patch_status'])} | "
            f"{_fmt_cell(row['validation_status'])} | "
            f"{_fmt_cell(row['created_at'])} |"
        )
    return lines
