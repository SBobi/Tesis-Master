"""ExplanationAgent — third LLM agent in the thesis pipeline.

Receives the full CaseBundle context (update, execution, localization,
patch, validation) and produces a structured JSON explanation that is
parsed into an ExplanationEvidence object plus a rendered Markdown report.

Design constraints (thesis rules):
  - The agent NEVER accesses the DB or filesystem directly.
  - All I/O goes through parameters and return values.
  - Temperature is fixed at 0 for reproducibility.
  - Falls back to a deterministic explanation if JSON parsing fails.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..case_bundle.evidence import ExplanationEvidence
from ..utils.llm_provider import LLMProvider, LLMResponse
from ..utils.log import get_logger

log = get_logger(__name__)

AGENT_TYPE = "ExplanationAgent"

_SYSTEM_PROMPT = """\
You are the ExplanationAgent for a Kotlin Multiplatform (KMP) dependency repair pipeline.
Your task: given the full evidence record for one repair case, produce a structured explanation
that helps a human reviewer understand what was updated, why it broke, how it was localized
and repaired, and whether the repair succeeded.

Rules:
- Output ONLY valid JSON — no markdown fences, no prose before or after.
- The JSON must match the schema below exactly.
- Be honest about uncertainty: if the environment could not run iOS tests, say so.
- Keep each text field under 300 characters.

Output schema:
{
  "what_was_updated": "...",
  "update_class_rationale": "...",
  "localization_summary": "...",
  "patch_rationale": "...",
  "validation_summary": "...",
  "target_coverage_complete": true,
  "uncertainties": [
    {"kind": "environment", "description": "..."}
  ]
}

Valid uncertainty kinds: "environment", "localization", "patch", "validation".
Set target_coverage_complete to false if any target was NOT_RUN_ENVIRONMENT_UNAVAILABLE.
"""


@dataclass
class AgentExplanationOutput:
    """Parsed result from the ExplanationAgent LLM call."""
    evidence: ExplanationEvidence
    response: LLMResponse
    prompt: str


def run_explanation_agent(
    explanation_context: dict,
    provider: LLMProvider,
) -> AgentExplanationOutput:
    """Call the ExplanationAgent.

    Parameters
    ----------
    explanation_context:
        CaseBundle.explanation_context() — full evidence dict.
    provider:
        LLMProvider to use for the call.
    """
    prompt = _build_prompt(explanation_context)

    log.info("Calling %s (model=%s)", AGENT_TYPE, provider.model_id)
    t0 = time.monotonic()
    response = provider.complete(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
        max_tokens=2048,
        temperature=0.0,
    )
    log.info(
        "%s call complete: tokens_in=%d tokens_out=%d latency=%.2fs",
        AGENT_TYPE, response.tokens_in, response.tokens_out, response.latency_s,
    )

    evidence = _parse_response(response.content, explanation_context)
    evidence.model_id = response.model_id
    evidence.tokens_in = response.tokens_in
    evidence.tokens_out = response.tokens_out

    return AgentExplanationOutput(
        evidence=evidence,
        response=response,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(ctx: dict) -> str:
    update = ctx.get("update", {})
    exec_summary = ctx.get("execution_summary", {})
    localization = ctx.get("localization", {})
    patch = ctx.get("patch", {})
    validation = ctx.get("validation", {})

    # Version changes
    vc_lines = ""
    for vc in (update.get("version_changes") or []):
        vc_lines += (
            f"  - {vc.get('dependency_group', '?')}: "
            f"{vc.get('before', '?')} → {vc.get('after', '?')}\n"
        )
    vc_lines = vc_lines or "  (none)\n"

    # Localization top candidates
    candidates = (localization.get("candidates") or [])[:5]
    cand_lines = "\n".join(
        f"  {c.get('rank', i+1)}. {c.get('file_path', '?')} "
        f"[{c.get('source_set', '?')}] score={c.get('score', 0):.3f}"
        for i, c in enumerate(candidates)
    ) or "  (none)"

    # Validation targets
    target_results = (validation.get("target_results") or [])
    target_lines = "\n".join(
        f"  - {t.get('target', '?')}: {t.get('status', '?')}"
        for t in target_results
    ) or "  (not run)"

    # Patch info
    patch_status = patch.get("status", "NOT_RUN")
    diff_path = patch.get("diff_path", "(none)")

    return f"""\
## Dependency Update
- Update class: {update.get('update_class', '?')}
- Version changes:
{vc_lines}
## Execution Summary
- Before build: {exec_summary.get('before_status', 'NOT_RUN')}
- After build:  {exec_summary.get('after_status', 'NOT_RUN')}
- Error count:  {exec_summary.get('error_count', 0)}

## Localization (top candidates)
{cand_lines}

## Patch
- Status: {patch_status}
- Diff: {diff_path}

## Validation Results
{target_lines}
- Repository-level status: {validation.get('repository_level_status', 'NOT_RUN_YET')}

Produce the JSON explanation following the schema in the system prompt.
"""


# ---------------------------------------------------------------------------
# Response parsing and Markdown rendering
# ---------------------------------------------------------------------------


def _parse_response(content: str, ctx: dict) -> ExplanationEvidence:
    """Parse the LLM JSON response into ExplanationEvidence.

    Falls back to a deterministic explanation on parse failure.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.startswith("```"))

    try:
        data = json.loads(text)
        uncertainties = [
            ExplanationEvidence.Uncertainty(
                kind=u.get("kind", "environment"),
                description=u.get("description", ""),
            )
            for u in (data.get("uncertainties") or [])
        ]
        return ExplanationEvidence(
            what_was_updated=str(data.get("what_was_updated", "")),
            update_class_rationale=str(data.get("update_class_rationale", "")),
            localization_summary=str(data.get("localization_summary", "")),
            patch_rationale=str(data.get("patch_rationale", "")),
            validation_summary=str(data.get("validation_summary", "")),
            target_coverage_complete=bool(data.get("target_coverage_complete", False)),
            uncertainties=uncertainties,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning("Failed to parse ExplanationAgent response (%s) — using fallback", exc)
        return _deterministic_fallback(ctx)


def _deterministic_fallback(ctx: dict) -> ExplanationEvidence:
    """Build a minimal explanation from the context without the LLM."""
    update = ctx.get("update", {})
    exec_summary = ctx.get("execution_summary", {})
    validation = ctx.get("validation", {})

    vc = (update.get("version_changes") or [{}])[0]
    dep = vc.get("dependency_group", "unknown dependency")
    before = vc.get("before", "?")
    after = vc.get("after", "?")

    targets = validation.get("target_results") or []
    unavailable = [t for t in targets if "UNAVAILABLE" in str(t.get("status", ""))]
    coverage_complete = len(unavailable) == 0
    repo_status = str(validation.get("repository_level_status", "NOT_RUN_YET"))

    uncertainties = []
    if unavailable:
        names = ", ".join(t.get("target", "?") for t in unavailable)
        uncertainties.append(ExplanationEvidence.Uncertainty(
            kind="environment",
            description=f"Target(s) {names} could not be validated (environment unavailable).",
        ))

    return ExplanationEvidence(
        what_was_updated=f"{dep} updated from {before} to {after}.",
        update_class_rationale=str(update.get("update_class", "UNKNOWN")),
        localization_summary=f"Build failed after update with {exec_summary.get('error_count', 0)} error(s).",
        patch_rationale="Patch generated by RepairAgent.",
        validation_summary=f"Repository-level validation status: {repo_status}.",
        target_coverage_complete=coverage_complete,
        uncertainties=uncertainties,
    )


def render_markdown(evidence: ExplanationEvidence, case_id: str) -> str:
    """Render ExplanationEvidence to a Markdown report."""
    lines = [
        f"# Repair Explanation — Case `{case_id[:8]}`\n",
        f"## What Was Updated\n{evidence.what_was_updated}\n",
        f"## Update Classification\n{evidence.update_class_rationale}\n",
        f"## Impact Localization\n{evidence.localization_summary}\n",
        f"## Patch Rationale\n{evidence.patch_rationale}\n",
        f"## Validation\n{evidence.validation_summary}\n",
    ]
    if evidence.uncertainties:
        lines.append("## Uncertainties\n")
        for u in evidence.uncertainties:
            lines.append(f"- **{u.kind}**: {u.description}\n")
    coverage = "Yes" if evidence.target_coverage_complete else "No (some targets not validated)"
    lines.append(f"\n---\n*Target coverage complete: {coverage}*\n")
    if evidence.model_id:
        lines.append(
            f"*Generated by {AGENT_TYPE} using `{evidence.model_id}` "
            f"(tokens in: {evidence.tokens_in}, out: {evidence.tokens_out})*\n"
        )
    return "\n".join(lines)
