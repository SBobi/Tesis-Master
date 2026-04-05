"""Baseline repair modes for thesis evaluation.

Four modes (from the thesis evaluation plan):
  raw_error              — only dep diff + raw compiler errors
  context_rich           — adds localized files + source-set info
  iterative_agentic      — iterates up to max_attempts with retry guidance
  full_thesis            — complete staged pipeline (full evidence model)

All modes use the same RepairAgent; what differs is the context passed to
the prompt builder and the iteration strategy.
"""

from .baseline_runner import run_baseline, BASELINE_MODES

__all__ = ["run_baseline", "BASELINE_MODES"]
