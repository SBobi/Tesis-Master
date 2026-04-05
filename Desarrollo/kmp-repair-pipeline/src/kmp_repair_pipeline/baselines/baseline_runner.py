"""Run one or all baseline repair modes for a repair case.

Used for the thesis evaluation: same case, four different repair strategies,
all results persisted to patch_attempts with the repair_mode column set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..repair.repairer import RepairRunResult, repair
from ..utils.llm_provider import LLMProvider
from ..utils.log import get_logger

log = get_logger(__name__)

BASELINE_MODES = ("raw_error", "context_rich", "iterative_agentic", "full_thesis")

# iterative_agentic iterates until a patch applies or max_attempts is reached
_ITERATIVE_MAX_ATTEMPTS = 3


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
) -> BaselineRunResult:
    """Run a single baseline mode for `case_id`.

    For ``iterative_agentic``: retries up to ``_ITERATIVE_MAX_ATTEMPTS`` times,
    stopping early when a patch applies successfully.
    """
    if mode not in BASELINE_MODES:
        raise ValueError(f"Unknown baseline mode: {mode!r}. Choose from {BASELINE_MODES}")

    result = BaselineRunResult(case_id=case_id, mode=mode)

    if mode == "iterative_agentic":
        for _ in range(_ITERATIVE_MAX_ATTEMPTS):
            run = repair(
                case_id=case_id,
                session=session,
                artifact_base=artifact_base,
                repair_mode=mode,
                provider=provider,
                top_k=top_k,
                max_attempts=_ITERATIVE_MAX_ATTEMPTS,
                patch_strategy=patch_strategy,
                force_patch_attempt=force_patch_attempt,
            )
            result.results.append(run)
            if run.patch_status == "APPLIED":
                log.info(
                    "Iterative mode: patch applied on attempt %d", run.attempt_number
                )
                break
            log.info(
                "Iterative mode: attempt %d status=%s — retrying",
                run.attempt_number, run.patch_status,
            )
    else:
        run = repair(
            case_id=case_id,
            session=session,
            artifact_base=artifact_base,
            repair_mode=mode,
            provider=provider,
            top_k=top_k,
            patch_strategy=patch_strategy,
            force_patch_attempt=force_patch_attempt,
        )
        result.results.append(run)

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
) -> dict[str, BaselineRunResult]:
    """Run multiple baseline modes and return a mapping of mode → result."""
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
        )
    return results
