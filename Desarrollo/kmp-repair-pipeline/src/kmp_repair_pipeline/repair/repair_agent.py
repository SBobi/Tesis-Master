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

CRITICAL — Build-file fixes:
7. Errors of type KLIB_ABI_ERROR mean the library's Kotlin/Native KLIB was
   compiled with a different Kotlin version than the project uses.  The fix is
   almost always to update the `kotlin` version alias in
   gradle/libs.versions.toml — NOT to edit .kt source files.
   Example fix for a KLIB_ABI_ERROR:
     --- a/gradle/libs.versions.toml
     +++ b/gradle/libs.versions.toml
     @@ -1,5 +1,5 @@
      [versions]
     -kotlin = "2.2.0"
     +kotlin = "2.3.0"
8. Always check the Current Version Catalog section in the prompt before
   deciding whether a build-file version bump fixes the error.
9. NEVER lower (downgrade) any version number in libs.versions.toml or any
   build file. If your diff decreases a version string (e.g. "2.3.0" → "2.1.0"),
   it is wrong. Only bumps (increases) are valid fixes.
"""

_FORCE_PATCH_APPENDIX = """\

## Strict Output Requirement
Return a best-effort unified diff and do NOT output PATCH_IMPOSSIBLE.
Modify at least one file from the localized file list.
If unsure, make the smallest safe compatibility patch possible.
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
    force_patch_attempt: bool = False,
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
    prompt = _build_prompt(
        repair_context,
        attempt_number,
        repair_mode,
        force_patch_attempt=force_patch_attempt,
    )
    system_prompt = _SYSTEM_PROMPT
    if force_patch_attempt:
        system_prompt += "\n7. For this call, you MUST return a unified diff and MUST NOT return PATCH_IMPOSSIBLE."

    log.info(
        "Calling %s (model=%s mode=%s attempt=%d)",
        AGENT_TYPE, provider.model_id, repair_mode, attempt_number,
    )
    response = provider.complete(
        prompt=prompt,
        system=system_prompt,
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


def _build_prompt(
    context: dict,
    attempt_number: int,
    mode: str,
    force_patch_attempt: bool = False,
) -> str:
    if mode == "raw_error":
        base = _prompt_raw_error(context, attempt_number)
    elif mode == "context_rich":
        base = _prompt_context_rich(context, attempt_number)
    else:
        # full_thesis and iterative_agentic use the same rich prompt
        base = _prompt_full_thesis(context, attempt_number)

    if force_patch_attempt:
        return f"{base}\n{_FORCE_PATCH_APPENDIX}"
    return base


def _prompt_raw_error(context: dict, attempt: int) -> str:
    """Minimal baseline: dependency diff + raw compiler errors only.

    No file content — tests whether error messages alone suffice.
    """
    update = context.get("update", {})
    errors = context.get("errors", [])
    required_kotlin = context.get("required_kotlin_version")
    cascade = context.get("kotlin_cascade_constraints", {})
    version_catalog = context.get("version_catalog", {})
    vcs = update.get("version_changes", [])
    pr_title = update.get("update_event", {}).get("pr_title") or ""
    vc_lines = "\n".join(
        f"  {v.get('dependency_group', '?')}: {v.get('before', '?')} → {v.get('after', '?')}"
        for v in vcs
    )
    error_lines = _format_errors(errors)
    pr_line = f"PR: {pr_title}\n" if pr_title else ""
    kotlin_section = _format_required_kotlin_version(required_kotlin, cascade, version_catalog)
    return f"""\
## Dependency Update (attempt {attempt})
{pr_line}{vc_lines or "  (unknown)"}
{kotlin_section}
## Compiler Errors
{error_lines}

Produce a unified diff to fix these errors.
"""


def _prompt_context_rich(context: dict, attempt: int) -> str:
    """Richer baseline: adds localized files, source-set info, and file contents."""
    update = context.get("update", {})
    errors = context.get("errors", [])
    localized = context.get("localized_files", [])
    required_kotlin = context.get("required_kotlin_version")
    cascade = context.get("kotlin_cascade_constraints", {})
    file_contents: dict[str, str] = context.get("file_contents", {})
    build_file_contents: dict[str, str] = context.get("build_file_contents", {})
    version_catalog: dict[str, str] = context.get("version_catalog", {})
    vcs = update.get("version_changes", [])
    pr_title = update.get("update_event", {}).get("pr_title") or ""
    vc_lines = "\n".join(
        f"  {v.get('dependency_group', '?')}: {v.get('before', '?')} → {v.get('after', '?')}"
        for v in vcs
    )
    error_lines = _format_errors(errors)
    file_list = "\n".join(f"  - {f}" for f in localized[:15]) or "  (none identified)"
    file_content_section = _format_file_contents(file_contents, build_file_contents)
    catalog_section = _format_version_catalog(version_catalog)
    kotlin_section = _format_required_kotlin_version(required_kotlin, cascade, version_catalog)
    pr_line = f"PR: {pr_title}\n" if pr_title else ""
    return f"""\
## Dependency Update (attempt {attempt})
{pr_line}{vc_lines or "  (unknown)"}
Update class: {update.get("update_class", "?")}
{kotlin_section}{catalog_section}
## Compiler Errors
{error_lines}

## Files Most Likely Needing Changes
{file_list}
{file_content_section}
Produce a unified diff to fix these errors, focusing on the files listed above.
CRITICAL: Use the EXACT content shown in the file sections above.
  - The diff context lines MUST match the actual file content character-for-character.
  - Never invent old values — only change lines that appear verbatim in the shown content.
  - For libs.versions.toml: the current kotlin value is shown in the Version Catalog above.
If any error is KLIB_ABI_ERROR, update the `kotlin` version in gradle/libs.versions.toml.
"""


def _prompt_full_thesis(context: dict, attempt: int) -> str:
    """Full thesis prompt: all evidence including previous attempts and file contents."""
    update = context.get("update", {})
    errors = context.get("errors", [])
    localized = context.get("localized_files", [])
    previous = context.get("previous_attempts", [])
    required_kotlin = context.get("required_kotlin_version")
    cascade = context.get("kotlin_cascade_constraints", {})
    file_contents: dict[str, str] = context.get("file_contents", {})
    build_file_contents: dict[str, str] = context.get("build_file_contents", {})
    version_catalog: dict[str, str] = context.get("version_catalog", {})
    vcs = update.get("version_changes", [])
    pr_title = update.get("update_event", {}).get("pr_title") or ""

    vc_lines = "\n".join(
        f"  {v.get('dependency_group', '?')}: {v.get('before', '?')} → {v.get('after', '?')}"
        for v in vcs
    )
    error_lines = _format_errors(errors)
    file_list = "\n".join(f"  - {f}" for f in localized[:15]) or "  (none identified)"
    file_content_section = _format_file_contents(file_contents, build_file_contents)
    catalog_section = _format_version_catalog(version_catalog)
    kotlin_section = _format_required_kotlin_version(required_kotlin, cascade, version_catalog)
    pr_line = f"PR: {pr_title}\n" if pr_title else ""

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
{pr_line}{vc_lines or "  (unknown)"}
Update class: {update.get("update_class", "?")}
{kotlin_section}{catalog_section}
## Compilation/Test Errors
{error_lines}

## Localized Files (ranked by impact score)
{file_list}
{file_content_section}{previous_section}
Produce a unified diff that fixes ALL listed errors.
Rules:
1. Respect KMP source-set boundaries (expect/actual symmetry).
2. CRITICAL — Use EXACT file content: every context line in your diff MUST match
   the file content shown above verbatim. Never invent lines that are not in the
   shown content. If you are unsure of a line, omit it from the diff context.
3. For gradle/libs.versions.toml: the Required Kotlin Version block above shows the
   EXACT current value and target. Use those values for the -/+ diff lines.
4. For each source file you modify: copy the exact import/code lines from the
   "Source Files" section above — do not guess or reconstruct from memory.
5. If any error is KLIB_ABI_ERROR or JVM metadata incompatibility, the PRIMARY fix
   is the kotlin version bump in gradle/libs.versions.toml.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_required_kotlin_version(
    required_version: str | None,
    cascade: dict | None = None,
    version_catalog: dict | None = None,
) -> str:
    """Render a high-priority block when the exact required Kotlin version is known.

    `required_version` is the MAX across all KLIB warnings (w: lines) and JVM
    metadata errors.  Using the max guarantees all libraries are satisfied.
    `cascade` shows each library's constraint so the agent can reason about it.
    `version_catalog` provides the ACTUAL current value of `kotlin` so the diff
    context line is precise (no hallucination of old values).
    """
    if not required_version:
        return ""

    current_kotlin = (version_catalog or {}).get("kotlin", "?")
    lines = [
        "## !! REQUIRED KOTLIN VERSION (extracted from compiler output) !!",
        f"  Current value in gradle/libs.versions.toml: kotlin = \"{current_kotlin}\"",
        f"  Required value (max across all library constraints): kotlin = \"{required_version}\"",
        f"  You MUST change: -kotlin = \"{current_kotlin}\"  →  +kotlin = \"{required_version}\"",
        "  Direction: UPWARD only — do NOT downgrade kotlin.",
    ]
    if cascade:
        lines.append("  Library-level constraints (all must be satisfied):")
        for lib, ver in sorted(cascade.items()):
            marker = " ← MAX (use this)" if ver == required_version else ""
            lines.append(f"    {lib}: requires Kotlin >= {ver}{marker}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _format_version_catalog(catalog: dict[str, str]) -> str:
    """Format the parsed libs.versions.toml [versions] section for the prompt.

    Highlights key KMP-related entries (kotlin, agp, compose, etc.) so the
    agent immediately sees which version may need to be bumped.
    """
    if not catalog:
        return ""

    # Key aliases that the agent should pay special attention to
    _KEY_ALIASES = {
        "kotlin", "kotlinx-coroutines", "kotlinx-serialization",
        "agp", "compose", "compose-multiplatform", "compose-bom",
        "ksp", "ktor", "coil", "koin", "room", "sqldelight",
        "navigation", "lifecycle", "jetpack",
    }

    lines = ["## Current Version Catalog (gradle/libs.versions.toml [versions])"]
    for key, val in sorted(catalog.items()):
        marker = " ★" if any(k in key.lower() for k in _KEY_ALIASES) else ""
        lines.append(f"  {key} = \"{val}\"{marker}")
    lines.append(
        "  (★ = KMP-critical alias — may need updating for ABI/API compatibility)"
    )
    return "\n".join(lines) + "\n"


def _format_file_contents(
    file_contents: dict[str, str],
    build_file_contents: dict[str, str],
) -> str:
    """Format file contents as a prompt section with code blocks."""
    parts: list[str] = []

    if build_file_contents:
        parts.append("\n## Build Files (check these for version/dependency fixes)")
        for path, content in build_file_contents.items():
            # Show only the filename for readability in the prompt
            short = path.split("/")[-1] if "/" in path else path
            parts.append(f"\n### {short}\n```\n{content}\n```")

    if file_contents:
        parts.append("\n## Source Files (current content)")
        for path, content in file_contents.items():
            short = path.split("/")[-1] if "/" in path else path
            lang = "kotlin" if path.endswith(".kt") else ""
            parts.append(f"\n### {short}\n```{lang}\n{content}\n```")

    return "\n".join(parts) if parts else ""


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
