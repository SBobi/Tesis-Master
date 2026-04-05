"""Generate evaluation reports in Markdown and JSON."""

from __future__ import annotations

from pathlib import Path

from ..domain.evaluation import EvaluationResult
from ..utils.json_io import save_json
from ..utils.log import get_logger

log = get_logger(__name__)


def generate_report(result: EvaluationResult, output_dir: str) -> None:
    """Generate Markdown and JSON evaluation reports."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    save_json(result, out / "evaluation.json")

    md = _build_markdown(result)
    md_path = out / "evaluation.md"
    md_path.write_text(md, encoding="utf-8")

    log.info(f"Evaluation reports saved to {out}")


def _build_markdown(r: EvaluationResult) -> str:
    lines = [
        f"# Evaluation Report — {r.scenario}",
        "",
        "## File-Level Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Precision | {r.precision:.4f} |",
        f"| Recall | {r.recall:.4f} |",
        f"| F1 Score | {r.f1:.4f} |",
        "",
        f"### True Positives ({len(r.true_positives)})",
        "",
    ]
    for f in r.true_positives:
        lines.append(f"- `{f}`")
    lines += ["", f"### False Positives ({len(r.false_positives)})", ""]
    for f in r.false_positives:
        lines.append(f"- `{f}`")
    lines += ["", f"### False Negatives ({len(r.false_negatives)})", ""]
    for f in r.false_negatives:
        lines.append(f"- `{f}`")
    lines += [
        "",
        "## Screen-Level Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Precision | {r.screen_precision:.4f} |",
        f"| Recall | {r.screen_recall:.4f} |",
        f"| F1 Score | {r.screen_f1:.4f} |",
        "",
    ]
    return "\n".join(lines)
