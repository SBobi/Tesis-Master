"""LocalizationAgent — LLM-backed re-ranking and source-set attribution.

Receives the deterministic candidate list + localization context from the
CaseBundle and asks the LLM to:
  1. Confirm or adjust rankings
  2. Assign source-set classifications
  3. Flag uncertain candidates

The agent's output is a JSON structure that is parsed back into a
LocalizationResult. Prompt and response are written to the ArtifactStore
and logged to agent_logs.

Design constraints (thesis rules):
  - The agent NEVER accesses the DB or filesystem directly.
  - All I/O goes through parameters and return values.
  - Prompt is built from the CaseBundle.localization_context() — no raw repo.
  - Temperature is fixed at 0 for reproducibility.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..case_bundle.evidence import LocalizationResult
from ..localization.scoring import ScoredCandidate
from ..utils.llm_provider import LLMProvider, LLMResponse
from ..utils.log import get_logger

log = get_logger(__name__)

AGENT_TYPE = "LocalizationAgent"

# System prompt — instructs the model on its role and output format
_SYSTEM_PROMPT = """\
You are the LocalizationAgent for a Kotlin Multiplatform (KMP) dependency repair pipeline.
Your task: given evidence about a dependency update that broke a KMP build, identify and \
rank the source files most likely to need changes.

Rules:
- Output ONLY valid JSON — no markdown fences, no prose before or after.
- The JSON must match the schema below exactly.
- Rank candidates from most to least likely to need changes (rank 1 = highest priority).
- Use "shared_code", "platform_specific", "build_level", or "uncertain" for classification.
- If you are unsure about a candidate, set confidence < 0.5 and classification "uncertain".

Output schema:
{
  "candidates": [
    {
      "rank": 1,
      "file_path": "src/commonMain/kotlin/App.kt",
      "source_set": "common",
      "classification": "shared_code",
      "score": 0.95,
      "rationale": "Direct import of changed API"
    }
  ],
  "agent_notes": "Brief summary of localization decision"
}
"""


@dataclass
class AgentLocalizationOutput:
    """Parsed result from the LLM response."""
    candidates: list[LocalizationResult.Candidate]
    agent_notes: str
    response: LLMResponse
    prompt: str


def run_localization_agent(
    localization_context: dict,
    deterministic_candidates: list[ScoredCandidate],
    provider: LLMProvider,
    max_candidates_in_prompt: int = 20,
) -> AgentLocalizationOutput:
    """Call the LLM to re-rank deterministic candidates.

    Parameters
    ----------
    localization_context:
        CaseBundle.localization_context() — execution errors + structural evidence.
    deterministic_candidates:
        Ranked candidates from the deterministic scorer (top N passed to prompt).
    provider:
        LLMProvider to use for the call.
    max_candidates_in_prompt:
        Cap the candidate list sent to the LLM to avoid token overflow.
    """
    prompt = _build_prompt(
        localization_context,
        deterministic_candidates[:max_candidates_in_prompt],
    )

    log.info("Calling %s (model=%s)", AGENT_TYPE, provider.model_id)
    t0 = time.monotonic()
    response = provider.complete(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.0,
    )
    log.info(
        "%s call complete: tokens_in=%d tokens_out=%d latency=%.2fs",
        AGENT_TYPE, response.tokens_in, response.tokens_out, response.latency_s,
    )

    candidates = _parse_response(response.content, deterministic_candidates)
    notes = _extract_notes(response.content)

    return AgentLocalizationOutput(
        candidates=candidates,
        agent_notes=notes,
        response=response,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(
    context: dict,
    candidates: list[ScoredCandidate],
) -> str:
    update_info = context.get("update", {})
    errors = context.get("execution_errors", [])
    structural = context.get("structural", {})

    dep_group = ""
    version_before = ""
    version_after = ""
    if update_info.get("version_changes"):
        vc = update_info["version_changes"][0]
        dep_group = vc.get("dependency_group", "")
        version_before = vc.get("before", "")
        version_after = vc.get("after", "")

    error_lines = "\n".join(
        f"  - [{e.get('error_type', 'ERROR')}] {e.get('file_path', '?')}:{e.get('line', '?')}: "
        f"{e.get('message', '')}"
        for e in errors[:30]
    )
    direct_imports = structural.get("direct_import_files", [])
    expect_actual = structural.get("expect_actual_pairs", [])
    build_files = structural.get("relevant_build_files", [])

    candidate_lines = "\n".join(
        f"  {i + 1}. {c.file_path} [source_set={c.source_set}] "
        f"score={c.final_score:.3f} "
        f"relation={c.score_breakdown.get('relation', '?')} "
        f"errors={c.score_breakdown.get('error_count', 0)}"
        for i, c in enumerate(candidates)
    )

    return f"""\
## Dependency Update
- Library: {dep_group}
- Before: {version_before}
- After: {version_after}
- Update class: {update_info.get("update_class", "?")}

## Compilation Errors (after update)
{error_lines or "  (none captured)"}

## Structural Evidence
- Direct import files ({len(direct_imports)}):
{chr(10).join(f"  - {f}" for f in direct_imports[:15]) or "  (none)"}

- Expect/Actual pairs ({len(expect_actual)}):
{chr(10).join(f"  - expect={p.get('expect_fqcn', '?')} actual_files={p.get('actual_files', [])}" for p in expect_actual[:10]) or "  (none)"}

- Relevant build files: {", ".join(build_files) or "(none)"}

## Deterministic Candidate List (re-rank these)
{candidate_lines or "  (no candidates from static analysis)"}

Return the JSON output following the schema in the system prompt.
"""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_response(
    content: str,
    fallback_candidates: list[ScoredCandidate],
) -> list[LocalizationResult.Candidate]:
    """Parse LLM JSON output into LocalizationResult.Candidate list.

    Falls back to the deterministic candidates if parsing fails.
    """
    text = content.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        )

    try:
        data = json.loads(text)
        raw_candidates = data.get("candidates", [])
        if not raw_candidates:
            raise ValueError("Empty candidates list in response")

        result = []
        for item in raw_candidates:
            result.append(LocalizationResult.Candidate(
                rank=int(item.get("rank", len(result) + 1)),
                file_path=str(item.get("file_path", "")),
                source_set=str(item.get("source_set", "unknown")),
                classification=str(item.get("classification", "uncertain")),
                score=float(item.get("score", 0.5)),
                score_breakdown={"agent_rationale": item.get("rationale", "")},
            ))
        log.info("Agent returned %d candidates", len(result))
        return result

    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning("Failed to parse agent response (%s) — using deterministic candidates", exc)
        return _deterministic_to_result_candidates(fallback_candidates)


def _extract_notes(content: str) -> str:
    try:
        data = json.loads(content.strip())
        return data.get("agent_notes", "")
    except Exception:
        return ""


def _deterministic_to_result_candidates(
    candidates: list[ScoredCandidate],
) -> list[LocalizationResult.Candidate]:
    return [
        LocalizationResult.Candidate(
            rank=i + 1,
            file_path=c.file_path,
            source_set=c.source_set,
            classification=c.classification,
            score=c.final_score,
            score_breakdown=c.score_breakdown,
        )
        for i, c in enumerate(candidates)
    ]
