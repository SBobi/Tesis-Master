"""Source code metrics: RLOC, function count, heuristic McCabe complexity.

Ported from prototype phase2_static/source_metrics.py with package rename.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..domain.analysis import SourceMetrics

_BRANCH_KEYWORDS = re.compile(r"\b(if|else|when|for|while|catch|&&|\|\|)\b")
_FUN_RE = re.compile(r"^\s*(override\s+)?fun\s+", re.MULTILINE)


def compute_metrics(file_path: str) -> SourceMetrics:
    """Compute lightweight source metrics for a Kotlin file."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return SourceMetrics()

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return SourceMetrics()

    lines = content.splitlines()

    rloc = 0
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if stripped.startswith("//") or not stripped:
            continue
        rloc += 1

    functions = len(_FUN_RE.findall(content))
    mcc = 1 + len(_BRANCH_KEYWORDS.findall(content))

    return SourceMetrics(rloc=rloc, functions=functions, mcc=mcc)
