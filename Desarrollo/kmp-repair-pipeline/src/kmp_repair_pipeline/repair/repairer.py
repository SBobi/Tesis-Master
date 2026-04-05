"""Phase 9 orchestrator — patch synthesis for one repair case.

Flow:
  1. Rehydrate CaseBundle from DB (must be LOCALIZED)
  2. Build repair_context() (restricted to localized evidence)
  3. Call RepairAgent with the chosen repair_mode
  4. Write diff to ArtifactStore
  5. Apply the diff to the after-clone workspace
  6. Persist patch_attempt row (with artifact paths + LLM metadata)
  7. Log agent call to agent_logs
  8. Advance bundle status → PATCH_ATTEMPTED

The caller controls the session commit.

Iterative mode: call repair() multiple times with increasing attempt_number;
each call reads previous_attempts from the bundle to avoid repeating failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.evidence import PatchAttempt
from ..case_bundle.serialization import from_db_case, to_db
from ..repair.patch_applier import apply_patch
from ..repair.repair_agent import AGENT_TYPE, AgentRepairOutput, run_repair_agent
from ..storage.artifact_store import ArtifactStore
from ..storage.repositories import (
    AgentLogRepo,
    PatchAttemptRepo,
    RepairCaseRepo,
    RevisionRepo,
)
from ..utils.llm_provider import LLMProvider, get_default_provider
from ..utils.log import get_logger

log = get_logger(__name__)

# Valid repair modes (thesis baselines + full pipeline)
REPAIR_MODES = ("full_thesis", "raw_error", "context_rich", "iterative_agentic")


@dataclass
class RepairRunResult:
    bundle: CaseBundle
    attempt_number: int
    repair_mode: str
    patch_status: str          # "APPLIED" | "FAILED_APPLY" | "IMPOSSIBLE"
    diff_path: Optional[str]
    touched_files: list[str]
    used_agent: bool = True


def repair(
    case_id: str,
    session: Session,
    artifact_base: Path | str = Path("data/artifacts"),
    repair_mode: str = "full_thesis",
    provider: Optional[LLMProvider] = None,
    top_k: int = 5,
    max_attempts: int = 3,
) -> RepairRunResult:
    """Synthesize and apply one repair patch for `case_id`.

    For iterative_agentic mode, call this function multiple times — previous
    attempts are automatically included in the prompt via the bundle.

    Parameters
    ----------
    case_id:
        UUID of a repair case that has been localized (status LOCALIZED).
    session:
        Active SQLAlchemy session (caller controls commit).
    artifact_base:
        Root of the artifact store.
    repair_mode:
        One of: full_thesis, raw_error, context_rich, iterative_agentic.
    provider:
        LLM provider override. None → get_default_provider().
    top_k:
        Number of localized files to include in the repair context.
    max_attempts:
        For iterative_agentic: max total attempts before giving up.
    """
    if repair_mode not in REPAIR_MODES:
        raise ValueError(f"repair_mode must be one of {REPAIR_MODES}, got {repair_mode!r}")

    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    # Determine attempt number
    existing_attempts = PatchAttemptRepo(session).list_for_case(case_id)
    same_mode_attempts = [a for a in existing_attempts if a.repair_mode == repair_mode]
    attempt_number = len(same_mode_attempts) + 1

    if repair_mode == "iterative_agentic" and attempt_number > max_attempts:
        log.warning(
            "Case %s: max attempts (%d) reached for mode %s",
            case_id[:8], max_attempts, repair_mode,
        )

    # Locate after-clone
    after_rev = RevisionRepo(session).get(case_id, "after")
    if after_rev is None or not after_rev.local_path:
        raise ValueError(f"Case {case_id}: after revision not cloned — run build-case first")
    after_path = Path(after_rev.local_path)

    artifact_store = ArtifactStore(artifact_base, case_id)
    llm = provider or get_default_provider()

    # Build repair context
    ctx = bundle.repair_context(top_k=top_k)

    # --- Agent call -------------------------------------------------------
    agent_out: AgentRepairOutput = run_repair_agent(
        repair_context=ctx,
        provider=llm,
        attempt_number=attempt_number,
        repair_mode=repair_mode,
    )

    call_index = _next_agent_call_index(case_id, session)

    # Persist prompt + response
    prompt_path, prompt_sha = artifact_store.write_prompt(AGENT_TYPE, call_index, agent_out.prompt)
    response_path, response_sha = artifact_store.write_response(
        AGENT_TYPE, call_index, agent_out.response.content
    )

    # Log to agent_logs
    AgentLogRepo(session).create(
        repair_case_id=case_id,
        agent_type=AGENT_TYPE,
        call_index=call_index,
        model_id=agent_out.response.model_id,
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha,
        response_path=response_path,
        response_sha256=response_sha,
        tokens_in=agent_out.response.tokens_in,
        tokens_out=agent_out.response.tokens_out,
        latency_s=agent_out.response.latency_s,
    )

    # --- Determine patch status -------------------------------------------
    if agent_out.is_impossible:
        log.warning("Case %s attempt %d: agent reported PATCH_IMPOSSIBLE", case_id[:8], attempt_number)
        patch_status = "IMPOSSIBLE"
        diff_path_str: Optional[str] = None
        diff_sha: Optional[str] = None
        touched: list[str] = []
    else:
        # Write diff to artifact store
        diff_path, diff_sha = artifact_store.write_patch(attempt_number, repair_mode, agent_out.diff_text)
        diff_path_str = diff_path

        # Apply to after-clone
        apply_result = apply_patch(agent_out.diff_text, after_path)
        if apply_result.success:
            patch_status = "APPLIED"
            log.info(
                "Case %s attempt %d: patch applied (%d files)",
                case_id[:8], attempt_number, len(apply_result.touched_files),
            )
        else:
            patch_status = "FAILED_APPLY"
            log.warning(
                "Case %s attempt %d: patch failed to apply — %s",
                case_id[:8], attempt_number, apply_result.stderr[:200],
            )
        touched = apply_result.touched_files or agent_out.touched_files

    # --- Persist patch_attempt row ----------------------------------------
    attempt_row = PatchAttemptRepo(session).create(
        repair_case_id=case_id,
        attempt_number=attempt_number,
        repair_mode=repair_mode,
        model_id=agent_out.response.model_id,
    )
    # Update mutable fields
    attempt_row.status = patch_status
    attempt_row.diff_path = diff_path_str
    attempt_row.diff_sha256 = diff_sha if not agent_out.is_impossible else None
    attempt_row.touched_files = touched
    attempt_row.prompt_path = prompt_path
    attempt_row.prompt_sha256 = prompt_sha
    attempt_row.response_path = response_path
    attempt_row.response_sha256 = response_sha
    attempt_row.tokens_in = agent_out.response.tokens_in
    attempt_row.tokens_out = agent_out.response.tokens_out
    session.flush()

    # --- Update bundle ----------------------------------------------------
    bundle_attempt = PatchAttempt(
        attempt_number=attempt_number,
        repair_mode=repair_mode,
        status=patch_status,
        diff_text=agent_out.diff_text if not agent_out.is_impossible else "",
        diff_path=diff_path_str,
        diff_sha256=diff_sha if not agent_out.is_impossible else None,
        touched_files=touched,
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha,
        response_path=response_path,
        response_sha256=response_sha,
        model_id=agent_out.response.model_id,
        tokens_in=agent_out.response.tokens_in,
        tokens_out=agent_out.response.tokens_out,
    )
    bundle.add_patch_attempt(bundle_attempt)
    to_db(bundle, session)

    RepairCaseRepo(session).set_status(
        RepairCaseRepo(session).get_by_id(case_id), "PATCH_ATTEMPTED"
    )

    log.info(
        "Case %s repair done: mode=%s attempt=%d status=%s",
        case_id[:8], repair_mode, attempt_number, patch_status,
    )

    return RepairRunResult(
        bundle=bundle,
        attempt_number=attempt_number,
        repair_mode=repair_mode,
        patch_status=patch_status,
        diff_path=diff_path_str,
        touched_files=touched,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_agent_call_index(case_id: str, session: Session) -> int:
    from sqlalchemy import select, func
    from ..storage.models import AgentLog

    stmt = select(func.count()).where(
        AgentLog.repair_case_id == case_id,
        AgentLog.agent_type == AGENT_TYPE,
    )
    return session.scalar(stmt) or 0
