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
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.evidence import PatchAttempt
from ..case_bundle.serialization import from_db_case, to_db
from ..repair.patch_applier import apply_patch, extract_touched_files, revert_patch
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
from ..utils.workspace_lock import WorkspaceLock

log = get_logger(__name__)

# Valid repair modes (thesis baselines + full pipeline)
REPAIR_MODES = ("full_thesis", "raw_error", "context_rich", "iterative_agentic")
PATCH_STRATEGIES = ("single_diff", "chain_by_file")


@dataclass
class RepairRunResult:
    bundle: CaseBundle
    attempt_number: int
    repair_mode: str
    patch_strategy: str
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
    patch_strategy: str = "single_diff",
    force_patch_attempt: bool = True,
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
    if patch_strategy not in PATCH_STRATEGIES:
        raise ValueError(f"patch_strategy must be one of {PATCH_STRATEGIES}, got {patch_strategy!r}")

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

    # Acquire workspace lock — prevents concurrent repair/validate from
    # corrupting the git workspace with interleaved patch operations.
    with WorkspaceLock(after_path):
        return _repair_inner(
            case_id=case_id,
            session=session,
            bundle=bundle,
            after_path=after_path,
            artifact_store=artifact_store,
            llm=llm,
            attempt_number=attempt_number,
            repair_mode=repair_mode,
            patch_strategy=patch_strategy,
            force_patch_attempt=force_patch_attempt,
            top_k=top_k,
        )


def _repair_inner(
    case_id: str,
    session,
    bundle,
    after_path: Path,
    artifact_store,
    llm,
    attempt_number: int,
    repair_mode: str,
    patch_strategy: str,
    force_patch_attempt: bool,
    top_k: int = 5,
) -> "RepairRunResult":
    """Core repair logic — runs inside the workspace lock."""
    # Build repair context
    ctx = bundle.repair_context(top_k=top_k)

    # Enrich context with actual file contents so the agent can generate
    # valid unified diffs (correct line numbers and context lines).
    ctx["file_contents"] = _read_file_contents(ctx.get("localized_files", []))

    # Build files — libs.versions.toml goes FIRST so the agent sees it before
    # source files.  This is the primary fix target for version-bump repairs
    # (KLIB ABI errors, AGP upgrades, etc.).
    build_files: list[str] = []
    # Prioritise libs.versions.toml
    for toml_rel in ("gradle/libs.versions.toml", "libs.versions.toml"):
        toml_abs = after_path / toml_rel
        if toml_abs.exists():
            build_files.append(str(toml_abs))
            break
    if bundle.structural and bundle.structural.relevant_build_files:
        for rel in bundle.structural.relevant_build_files:
            abs_p = after_path / rel
            if abs_p.exists() and str(abs_p) not in build_files:
                build_files.append(str(abs_p))
    ctx["build_file_contents"] = _read_file_contents(build_files)

    # --- Agent call -------------------------------------------------------
    agent_out: AgentRepairOutput = run_repair_agent(
        repair_context=ctx,
        provider=llm,
        attempt_number=attempt_number,
        repair_mode=repair_mode,
    )
    prompt_path, prompt_sha, response_path, response_sha = _persist_agent_call(
        case_id=case_id,
        session=session,
        artifact_store=artifact_store,
        agent_out=agent_out,
    )

    forced_retry_used = False
    if agent_out.is_impossible and force_patch_attempt:
        log.warning(
            "Case %s attempt %d: forcing best-effort patch retry after PATCH_IMPOSSIBLE",
            case_id[:8], attempt_number,
        )
        forced_retry_used = True
        agent_out = run_repair_agent(
            repair_context=ctx,
            provider=llm,
            attempt_number=attempt_number,
            repair_mode=repair_mode,
            force_patch_attempt=True,
        )
        prompt_path, prompt_sha, response_path, response_sha = _persist_agent_call(
            case_id=case_id,
            session=session,
            artifact_store=artifact_store,
            agent_out=agent_out,
        )

    # --- Determine patch status -------------------------------------------
    if agent_out.is_impossible:
        log.warning("Case %s attempt %d: agent reported PATCH_IMPOSSIBLE", case_id[:8], attempt_number)
        patch_status = "IMPOSSIBLE"
        diff_path_str: Optional[str] = None
        diff_sha: Optional[str] = None
        diff_text_for_attempt = ""
        touched: list[str] = []
        detail = "agent reported PATCH_IMPOSSIBLE"
        if forced_retry_used:
            detail = "agent reported PATCH_IMPOSSIBLE after forced patch retry"
        retry_reason = _strategy_retry_reason(patch_strategy, detail)
    else:
        diff_text_for_attempt = _normalize_model_diff_output(agent_out.diff_text)
        if diff_text_for_attempt != agent_out.diff_text:
            log.info(
                "Case %s attempt %d: normalized markdown-fenced diff before apply",
                case_id[:8], attempt_number,
            )

        # Write diff to artifact store
        diff_path, diff_sha = artifact_store.write_patch(attempt_number, repair_mode, diff_text_for_attempt)
        diff_path_str = diff_path

        # Validate diff syntax before applying to avoid malformed-patch attempts.
        precheck_ok, precheck_error = _precheck_unified_diff(diff_text_for_attempt)
        if not precheck_ok:
            patch_status = "FAILED_APPLY"
            touched = extract_touched_files(diff_text_for_attempt) or agent_out.touched_files
            precheck_detail = f"malformed diff precheck failed: {precheck_error}"
            if forced_retry_used:
                precheck_detail = f"forced patch retry used; {precheck_detail}"
            retry_reason = _strategy_retry_reason(
                patch_strategy,
                precheck_detail,
            )
            log.warning(
                "Case %s attempt %d: malformed diff rejected before apply (strategy=%s) — %s",
                case_id[:8], attempt_number, patch_strategy, precheck_error,
            )
        else:
            # Apply to after-clone
            if patch_strategy == "chain_by_file":
                apply_result = _apply_patch_chain_by_file(diff_text_for_attempt, after_path)
            else:
                apply_result = apply_patch(diff_text_for_attempt, after_path)
            if apply_result.success:
                patch_status = "APPLIED"
                if forced_retry_used:
                    retry_reason = _strategy_retry_reason(patch_strategy, "forced patch retry used")
                else:
                    retry_reason = _strategy_retry_reason(patch_strategy)
                log.info(
                    "Case %s attempt %d: patch applied (%d files, strategy=%s)",
                    case_id[:8], attempt_number, len(apply_result.touched_files), patch_strategy,
                )
            else:
                patch_status = "FAILED_APPLY"
                apply_detail = apply_result.stderr
                if forced_retry_used:
                    apply_detail = f"forced patch retry used; {apply_result.stderr}"
                retry_reason = _strategy_retry_reason(patch_strategy, apply_detail)
                log.warning(
                    "Case %s attempt %d: patch failed to apply (strategy=%s) — %s",
                    case_id[:8], attempt_number, patch_strategy, apply_result.stderr[:200],
                )
            touched = apply_result.touched_files or extract_touched_files(diff_text_for_attempt) or agent_out.touched_files

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
    attempt_row.retry_reason = retry_reason
    session.flush()

    # --- Update bundle ----------------------------------------------------
    bundle_attempt = PatchAttempt(
        attempt_number=attempt_number,
        repair_mode=repair_mode,
        status=patch_status,
        diff_text=diff_text_for_attempt if not agent_out.is_impossible else "",
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
        retry_reason=retry_reason,
    )
    bundle.add_patch_attempt(bundle_attempt)
    to_db(bundle, session)

    RepairCaseRepo(session).set_status(
        RepairCaseRepo(session).get_by_id(case_id), "PATCH_ATTEMPTED"
    )

    log.info(
        "Case %s repair done: mode=%s strategy=%s attempt=%d status=%s",
        case_id[:8], repair_mode, patch_strategy, attempt_number, patch_status,
    )

    return RepairRunResult(
        bundle=bundle,
        attempt_number=attempt_number,
        repair_mode=repair_mode,
        patch_strategy=patch_strategy,
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


def _persist_agent_call(
    case_id: str,
    session: Session,
    artifact_store: ArtifactStore,
    agent_out: AgentRepairOutput,
) -> tuple[str, str, str, str]:
    """Persist one agent prompt/response pair and create agent_log row."""
    call_index = _next_agent_call_index(case_id, session)
    prompt_path, prompt_sha = artifact_store.write_prompt(AGENT_TYPE, call_index, agent_out.prompt)
    response_path, response_sha = artifact_store.write_response(
        AGENT_TYPE, call_index, agent_out.response.content
    )

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
    return prompt_path, prompt_sha, response_path, response_sha


def _strategy_retry_reason(patch_strategy: str, detail: str | None = None) -> str:
    """Store strategy metadata in retry_reason for reporting and auditability."""
    base = f"patch_strategy={patch_strategy}"
    if not detail:
        return base
    compact = re.sub(r"\s+", " ", detail).strip()
    if len(compact) > 240:
        compact = compact[:237] + "..."
    return f"{base}; {compact}"


def _normalize_model_diff_output(diff_text: str) -> str:
    """Strip wrapping markdown code fences around unified diffs, when present."""
    stripped = diff_text.strip()
    if not stripped.startswith("```"):
        return diff_text

    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        inner = "\n".join(lines[1:-1]).strip("\n")
        return (inner + "\n") if inner else ""
    return diff_text


def _precheck_unified_diff(diff_text: str) -> tuple[bool, str]:
    """Lightweight syntax check for model-generated unified diffs.

    This prevents obvious malformed patches from reaching patch/git apply.
    """
    lines = diff_text.splitlines()
    if not lines:
        return False, "empty diff"

    saw_file = False
    expect_plus = False
    in_file = False
    in_hunk = False
    file_has_hunk = False

    allowed_pre_hunk_prefixes = (
        "diff --git ",
        "index ",
        "new file mode",
        "deleted file mode",
        "similarity index",
        "rename from ",
        "rename to ",
        "old mode ",
        "new mode ",
        "Binary files ",
    )

    for i, line in enumerate(lines, start=1):
        if line.startswith("--- "):
            if expect_plus:
                return False, f"line {i}: expected '+++' after previous '---'"
            if in_file and not file_has_hunk:
                return False, f"line {i}: file block missing hunk header '@@'"
            saw_file = True
            expect_plus = True
            in_file = True
            in_hunk = False
            file_has_hunk = False
            continue

        if expect_plus:
            if not line.startswith("+++ "):
                return False, f"line {i}: expected '+++' after '---'"
            expect_plus = False
            continue

        if not in_file:
            continue

        if line.startswith("@@ "):
            in_hunk = True
            file_has_hunk = True
            continue

        if in_hunk:
            if line.startswith((" ", "+", "-", "\\")):
                continue
            return False, f"line {i}: invalid hunk line '{line[:24]}'"

        if line.startswith(allowed_pre_hunk_prefixes) or not line.strip():
            continue
        return False, f"line {i}: expected hunk header '@@'"

    if expect_plus:
        return False, "diff ends after '---' without matching '+++'"
    if in_file and not file_has_hunk:
        return False, "final file block missing hunk header '@@'"
    if not saw_file:
        return False, "missing file headers ('---' / '+++')"
    return True, ""


def _split_diff_by_file(diff_text: str) -> list[str]:
    """Split a unified diff into per-file patch blocks."""
    lines = diff_text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    in_block = False

    for line in lines:
        if line.startswith("--- "):
            if in_block and current:
                blocks.append(current)
                current = []
            in_block = True
        if in_block:
            current.append(line)

    if in_block and current:
        blocks.append(current)

    rendered = ["\n".join(b) + "\n" for b in blocks if any(l.startswith("+++ ") for l in b)]
    return rendered


def _read_file_contents(paths: list[str], max_bytes: int = 8000) -> dict[str, str]:
    """Read file contents for context injection into repair prompts.

    Truncates large files to `max_bytes` to stay within token budgets.
    Returns a dict of path → content (skips unreadable files silently).
    When truncation occurs, the size information is logged and appended to the
    content so the agent knows it is working with a partial view.
    """
    contents: dict[str, str] = {}
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
            total_bytes = len(text.encode("utf-8"))
            if total_bytes > max_bytes:
                # Truncate on UTF-8 boundary to avoid mid-character cut
                encoded = text.encode("utf-8")[:max_bytes]
                text = encoded.decode("utf-8", errors="ignore")
                log.info(
                    "Truncating %s for repair prompt: %d bytes total, showing first %d bytes",
                    Path(p).name, total_bytes, max_bytes,
                )
                text = (
                    text
                    + f"\n... [truncated: showing {max_bytes} of {total_bytes} bytes]"
                )
            contents[p] = text
        except OSError:
            pass
    return contents


def _apply_patch_chain_by_file(diff_text: str, after_path: Path):
    """Apply diff file-by-file in sequence, stopping on first failure.

    On partial failure, reverts all previously applied blocks in reverse order
    so the workspace is left in a clean state relative to the pre-attempt state.
    """
    from ..repair.patch_applier import PatchApplicationResult

    blocks = _split_diff_by_file(diff_text)
    if not blocks:
        return apply_patch(diff_text, after_path)

    touched_union: list[str] = []
    rejected_union: list[str] = []
    last_stderr = ""
    method = "patch"
    applied_blocks: list[str] = []   # blocks successfully applied so far

    for idx, block in enumerate(blocks, start=1):
        result = apply_patch(block, after_path)
        method = result.method
        last_stderr = result.stderr

        for f in result.touched_files:
            if f not in touched_union:
                touched_union.append(f)
        for f in result.rejected_files:
            if f not in rejected_union:
                rejected_union.append(f)

        if not result.success:
            # Roll back all previously applied blocks (reverse order)
            for prev_block in reversed(applied_blocks):
                rev = revert_patch(prev_block, after_path)
                if not rev.success:
                    log.warning(
                        "chain_by_file rollback failed for a block — workspace may be dirty: %s",
                        rev.stderr[:120],
                    )
            return PatchApplicationResult(
                success=False,
                touched_files=touched_union,
                rejected_files=rejected_union,
                stdout=result.stdout,
                stderr=f"chain_by_file failed at block {idx}/{len(blocks)}: {last_stderr}",
                method=method,
            )

        applied_blocks.append(block)

    return PatchApplicationResult(
        success=True,
        touched_files=touched_union,
        rejected_files=rejected_union,
        stderr=last_stderr,
        method=method,
    )
