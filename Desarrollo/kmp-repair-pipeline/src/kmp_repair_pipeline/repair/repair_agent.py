"""RepairAgent — LLM-backed patch synthesis.

Receives the repair context from CaseBundle (localized files + errors +
previous attempts) and produces a unified diff that fixes the breaking change.

Design constraints (thesis rules):
  - Temperature=0 for reproducibility.
  - The agent NEVER writes files directly; it returns a diff string.
  - The context is restricted to what CaseBundle.repair_context() exposes.
  - Prompt and response are returned for the orchestrator to persist.
  - Retry guidance (previous REJECTED attempts) is included in the prompt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..utils.llm_provider import LLMProvider, LLMResponse
from ..utils.log import get_logger

log = get_logger(__name__)

AGENT_TYPE = "RepairAgent"

_SYSTEM_PROMPT = """\
You are the RepairAgent for a Kotlin Multiplatform (KMP) dependency repair pipeline.

Your task: produce a unified diff (patch) that fixes compilation/test failures \
caused by a dependency version update.

Rules:
1. Output ONLY the unified diff — no markdown fences, no prose before or after.
2. The diff must be in standard unified diff format:
   --- a/path/to/file.kt
   +++ b/path/to/file.kt
   @@ -line,count +line,count @@
   context
   -removed line
   +added line
3. Fix ONLY the errors listed — do not refactor unrelated code.
4. Respect KMP source-set boundaries:
   - Changes to expect declarations must have matching actual changes.
   - Do not move shared code to platform-specific source sets.
5. If you cannot produce a correct patch, output exactly: PATCH_IMPOSSIBLE
6. If you need to change multiple files, include all of them in one diff.
"""


@dataclass
class AgentRepairOutput:
    diff_text: str            # unified diff or "PATCH_IMPOSSIBLE"
    is_impossible: bool
    response: LLMResponse
    prompt: str
    touched_files: list[str]  # file paths mentioned in the diff


def run_repair_agent(
    repair_context: dict,
    provider: LLMProvider,
    attempt_number: int = 1,
    repair_mode: str = "full_thesis",
) -> AgentRepairOutput:
    """Call the LLM to synthesize a repair patch.

    Parameters
    ----------
    repair_context:
        CaseBundle.repair_context() — localized files, errors, previous attempts.
    provider:
        LLMProvider to use.
    attempt_number:
        Which attempt this is (1-indexed). Shown in the prompt for context.
    repair_mode:
        One of: full_thesis, raw_error, context_rich, iterative_agentic.
    """
    prompt = _build_prompt(repair_context, attempt_number, repair_mode)

    log.info(
        "Calling %s (model=%s mode=%s attempt=%d)",
        AGENT_TYPE, provider.model_id, repair_mode, attempt_number,
    )
    response = provider.complete(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
        max_tokens=8192,
        temperature=0.0,
    )
    log.info(
        "%s call complete: tokens_in=%d tokens_out=%d latency=%.2fs",
        AGENT_TYPE, response.tokens_in, response.tokens_out, response.latency_s,
    )

    diff_text = response.content.strip()
    is_impossible = diff_text == "PATCH_IMPOSSIBLE" or not diff_text
    touched = _extract_touched_files(diff_text) if not is_impossible else []

    return AgentRepairOutput(
        diff_text=diff_text,
        is_impossible=is_impossible,
        response=response,
        prompt=prompt,
        touched_files=touched,
    )


# ---------------------------------------------------------------------------
# Prompt builders (one per repair mode)
# ---------------------------------------------------------------------------


def _build_prompt(context: dict, attempt_number: int, mode: str) -> str:
    if mode == "raw_error":
        return _prompt_raw_error(context, attempt_number)
    if mode == "context_rich":
        return _prompt_context_rich(context, attempt_number)
    # full_thesis and iterative_agentic use the same rich prompt
    return _prompt_full_thesis(context, attempt_number)


def _prompt_raw_error(context: dict, attempt: int) -> str:
    """Minimal baseline: only dependency diff + raw compiler errors."""
    update = context.get("update", {})
    errors = context.get("errors", [])
    vcs = update.get("version_changes", [])
    vc_lines = "\n".join(
        f"  {v.get('dependency_group', '?')}: {v.get('before', '?')} → {v.get('after', '?')}"
        for v in vcs
    )
    error_lines = _format_errors(errors)
    return f"""\
## Dependency Update (attempt {attempt})
{vc_lines or "  (unknown)"}

## Compiler Errors
{error_lines}

Produce a unified diff to fix these errors.
"""


def _prompt_context_rich(context: dict, attempt: int) -> str:
    """Richer baseline: adds localized files and source-set info."""
    update = context.get("update", {})
    errors = context.get("errors", [])
    localized = context.get("localized_files", [])
    vcs = update.get("version_changes", [])
    vc_lines = "\n".join(
        f"  {v.get('dependency_group', '?')}: {v.get('before', '?')} → {v.get('after', '?')}"
        for v in vcs
    )
    error_lines = _format_errors(errors)
    file_list = "\n".join(f"  - {f}" for f in localized[:15]) or "  (none identified)"
    return f"""\
## Dependency Update (attempt {attempt})
{vc_lines or "  (unknown)"}
Update class: {update.get("update_class", "?")}

## Compiler Errors
{error_lines}

## Files Most Likely Needing Changes
{file_list}

Produce a unified diff to fix these errors, focusing on the files listed above.
"""


def _prompt_full_thesis(context: dict, attempt: int) -> str:
    """Full thesis prompt: all evidence including previous attempts."""
    update = context.get("update", {})
    errors = context.get("errors", [])
    localized = context.get("localized_files", [])
    previous = context.get("previous_attempts", [])
    vcs = update.get("version_changes", [])

    vc_lines = "\n".join(
        f"  {v.get('dependency_group', '?')}: {v.get('before', '?')} → {v.get('after', '?')}"
        for v in vcs
    )
    error_lines = _format_errors(errors)
    file_list = "\n".join(f"  - {f}" for f in localized[:15]) or "  (none identified)"

    previous_section = ""
    if previous:
        prev_lines = "\n".join(
            f"  Attempt {p.get('attempt', '?')}: status={p.get('status', '?')} "
            f"reason={p.get('reason', 'N/A')}"
            for p in previous
        )
        previous_section = f"\n## Previous Attempts (avoid repeating these)\n{prev_lines}\n"

    return f"""\
## Dependency Update (attempt {attempt})
{vc_lines or "  (unknown)"}
Update class: {update.get("update_class", "?")}

## Compilation/Test Errors
{error_lines}

## Localized Files (ranked by impact score)
{file_list}
{previous_section}
Produce a unified diff that fixes ALL listed errors. \
Respect KMP source-set boundaries (expect/actual symmetry).
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_errors(errors: list[dict]) -> str:
    if not errors:
        return "  (no errors captured)"
    lines = []
    for e in errors[:40]:
        fp = e.get("file_path") or "?"
        ln = e.get("line") or "?"
        msg = (e.get("message") or "")[:120]
        lines.append(f"  [{e.get('error_type', 'ERROR')}] {fp}:{ln}: {msg}")
    return "\n".join(lines)


def _extract_touched_files(diff_text: str) -> list[str]:
    """Extract file paths from +++ lines in a unified diff."""
    paths: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            # Remove b/ prefix from git diffs
            if path.startswith("b/"):
                path = path[2:]
            if path and path != "/dev/null":
                paths.append(path)
    return list(dict.fromkeys(paths))  # deduplicate preserving order
