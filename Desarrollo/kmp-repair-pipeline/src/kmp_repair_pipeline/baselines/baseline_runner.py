"""Run one or all baseline repair modes for a repair case.

Used for the thesis evaluation: same case, four different repair strategies,
all results persisted to patch_attempts with the repair_mode column set.

Workspace isolation guarantee
------------------------------
Each baseline mode must see the original (unpatched) after-clone workspace so
that a patch applied by mode N does not corrupt modes N+1..M.  Before every
mode run, ``_reset_workspace`` performs a ``git checkout -- . && git clean -fd``
on the after-clone.  This is a local operation on a private workspace copy —
it is intentional and safe.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..repair.repairer import RepairRunResult, repair
from ..storage.repositories import RevisionRepo
from ..utils.llm_provider import LLMProvider
from ..utils.log import get_logger

log = get_logger(__name__)

BASELINE_MODES = ("raw_error", "context_rich", "iterative_agentic", "full_thesis")

# Per-baseline iteration budgets (thesis-defined repair strategy tiers).
#
# Rationale:
#   raw_error       — minimal context; more iterations rarely help because the
#                     agent has no file content to work with; cap at 2.
#   context_rich    — has file content but no retry guidance; 3 attempts give
#                     the agent a chance to self-correct formatting issues.
#   iterative_agentic — full retry loop with previous-attempt feedback; 4
#                       attempts balance cost vs. repair coverage.
#   full_thesis     — richest context + all previous attempts visible; 5
#                     attempts to maximise thesis recall without exploding cost.
#
# Override at call time via the `max_attempts` parameter.
_MODE_BUDGETS: dict[str, int] = {
    "raw_error":          2,
    "context_rich":       3,
    "iterative_agentic":  4,
    "full_thesis":        5,
}


@dataclass
class BaselineRunResult:
    case_id: str
    mode: str
    results: list[RepairRunResult] = field(default_factory=list)

    @property
    def final_status(self) -> str:
        if not self.results:
            return "NOT_RUN"
        return self.results[-1].patch_status

    @property
    def applied(self) -> bool:
        return any(r.patch_status == "APPLIED" for r in self.results)


def run_baseline(
    case_id: str,
    session: Session,
    mode: str,
    artifact_base: Path | str = Path("data/artifacts"),
    provider: Optional[LLMProvider] = None,
    top_k: int = 5,
    patch_strategy: str = "single_diff",
    force_patch_attempt: bool = True,
    max_attempts: Optional[int] = None,
) -> BaselineRunResult:
    """Run a single baseline mode for `case_id`.

    All modes support multi-attempt repair loops, but with different budgets
    (see ``_MODE_BUDGETS``).  The loop stops early when a patch applies.

    Parameters
    ----------
    max_attempts:
        Override the per-mode budget.  ``None`` → use ``_MODE_BUDGETS[mode]``.
    """
    if mode not in BASELINE_MODES:
        raise ValueError(f"Unknown baseline mode: {mode!r}. Choose from {BASELINE_MODES}")

    budget = max_attempts if max_attempts is not None else _MODE_BUDGETS[mode]

    # Always start from a clean workspace so previous mode patches don't bleed in
    _reset_workspace(case_id, session)

    result = BaselineRunResult(case_id=case_id, mode=mode)

    for attempt_idx in range(budget):
        run = repair(
            case_id=case_id,
            session=session,
            artifact_base=artifact_base,
            repair_mode=mode,
            provider=provider,
            top_k=top_k,
            max_attempts=budget,
            patch_strategy=patch_strategy,
            force_patch_attempt=force_patch_attempt,
        )
        result.results.append(run)

        if run.patch_status == "APPLIED":
            log.info(
                "Baseline %s: patch APPLIED on attempt %d/%d",
                mode, attempt_idx + 1, budget,
            )
            break

        if attempt_idx + 1 < budget:
            log.info(
                "Baseline %s: attempt %d/%d status=%s — retrying",
                mode, attempt_idx + 1, budget, run.patch_status,
            )
            # Reset between retry attempts so each attempt sees a clean workspace
            _reset_workspace(case_id, session)
        else:
            log.info(
                "Baseline %s: budget exhausted after %d attempt(s), final=%s",
                mode, budget, run.patch_status,
            )

    log.info(
        "Baseline %s for case %s: %d attempt(s), final=%s",
        mode, case_id[:8], len(result.results), result.final_status,
    )
    return result


def run_all_baselines(
    case_id: str,
    session: Session,
    artifact_base: Path | str = Path("data/artifacts"),
    provider: Optional[LLMProvider] = None,
    top_k: int = 5,
    patch_strategy: str = "single_diff",
    force_patch_attempt: bool = True,
    modes: Optional[list[str]] = None,
    max_attempts: Optional[int] = None,
) -> dict[str, BaselineRunResult]:
    """Run multiple baseline modes and return a mapping of mode → result.

    Parameters
    ----------
    max_attempts:
        Override per-mode budgets for all selected modes.  ``None`` → use
        the per-mode defaults from ``_MODE_BUDGETS``.
    """
    selected = modes or list(BASELINE_MODES)
    results: dict[str, BaselineRunResult] = {}
    for mode in selected:
        results[mode] = run_baseline(
            case_id=case_id,
            session=session,
            mode=mode,
            artifact_base=artifact_base,
            provider=provider,
            top_k=top_k,
            patch_strategy=patch_strategy,
            force_patch_attempt=force_patch_attempt,
            max_attempts=max_attempts,
        )
    return results


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


def _reset_workspace(case_id: str, session: Session) -> None:
    """Reset the after-clone workspace to HEAD — discard any applied patches.

    Called before every baseline mode and between iterative retry attempts so
    that each attempt starts from the original (unpatched) state.
    """
    after_rev = RevisionRepo(session).get(case_id, "after")
    if after_rev is None or not after_rev.local_path:
        log.warning("Case %s: after revision not found — cannot reset workspace", case_id[:8])
        return

    workspace = Path(after_rev.local_path)
    if not (workspace / ".git").exists():
        log.warning(
            "Case %s: workspace %s is not a git repo — skipping reset",
            case_id[:8], workspace,
        )
        return

    try:
        # Discard tracked-file modifications
        subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        # Remove untracked files and directories (reject files, .orig, etc.)
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        log.info(
            "Case %s: workspace reset to HEAD (%s)",
            case_id[:8], workspace,
        )
    except subprocess.CalledProcessError as exc:
        log.warning(
            "Case %s: workspace reset failed: %s",
            case_id[:8], exc.stderr.decode(errors="replace").strip(),
        )
