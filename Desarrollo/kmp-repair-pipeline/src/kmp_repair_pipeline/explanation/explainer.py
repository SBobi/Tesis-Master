"""Phase 11 — Generate a reviewer-oriented explanation for a repair case.

Orchestration:
  1. Rehydrate CaseBundle from DB
  2. Build explanation_context() from the bundle
  3. Call ExplanationAgent (LLM)
  4. Write prompt + response artifacts; log to agent_logs
  5. Render Markdown from structured evidence
  6. Write JSON + Markdown to ArtifactStore
  7. Persist Explanation row
  8. Attach ExplanationEvidence to bundle → to_db()
  9. Advance case status → EXPLAINED
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.evidence import ExplanationEvidence
from ..case_bundle.serialization import from_db_case, to_db
from ..storage.artifact_store import ArtifactStore
from ..storage.repositories import (
    AgentLogRepo,
    ExplanationRepo,
    PatchAttemptRepo,
    RepairCaseRepo,
)
from ..utils.llm_provider import LLMProvider, get_default_provider
from ..utils.log import get_logger
from .explanation_agent import (
    AGENT_TYPE,
    AgentExplanationOutput,
    render_markdown,
    run_explanation_agent,
)

log = get_logger(__name__)


@dataclass
class ExplainResult:
    case_id: str
    json_path: Optional[str]
    markdown_path: Optional[str]
    model_id: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]


def explain(
    case_id: str,
    session: Session,
    artifact_base: Path | str = Path("data/artifacts"),
    provider: Optional[LLMProvider] = None,
) -> ExplainResult:
    """Generate and persist an explanation for *case_id*.

    Parameters
    ----------
    case_id:
        UUID of the repair case (should be in VALIDATED or PATCH_ATTEMPTED status).
    session:
        Active SQLAlchemy session (caller controls commit).
    artifact_base:
        Root of the local artifact store.
    provider:
        LLMProvider to use.  Defaults to ``get_default_provider()``.
    """
    if provider is None:
        provider = get_default_provider()

    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    ctx = bundle.explanation_context()

    # ── Call the agent ───────────────────────────────────────────────────────
    agent_out: AgentExplanationOutput = run_explanation_agent(ctx, provider)

    # ── Write prompt + response artifacts ───────────────────────────────────
    artifact_store = ArtifactStore(artifact_base, case_id)
    agent_log_repo = AgentLogRepo(session)

    call_index = _next_agent_call_index(case_id, session)
    prompt_path, prompt_sha = artifact_store.write_prompt(AGENT_TYPE, call_index, agent_out.prompt)
    response_path, response_sha = artifact_store.write_response(
        AGENT_TYPE, call_index, agent_out.response.content
    )

    agent_log_repo.create(
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

    # ── Render and write explanation artifacts ───────────────────────────────
    import json as _json

    evidence = agent_out.evidence
    json_content = _json.dumps(evidence.model_dump(), indent=2, default=str)
    markdown_content = render_markdown(evidence, case_id)

    json_path, json_sha = artifact_store.write_explanation_json(json_content)
    markdown_path, markdown_sha = artifact_store.write_explanation_markdown(markdown_content)

    evidence.json_path = json_path
    evidence.json_sha256 = json_sha
    evidence.markdown_path = markdown_path
    evidence.markdown_sha256 = markdown_sha

    # ── Resolve latest patch attempt (for FK) ───────────────────────────────
    all_attempts = PatchAttemptRepo(session).list_for_case(case_id)
    latest_attempt_id: Optional[str] = all_attempts[-1].id if all_attempts else None

    # ── Persist Explanation row ──────────────────────────────────────────────
    ExplanationRepo(session).create(
        repair_case_id=case_id,
        patch_attempt_id=latest_attempt_id,
        json_path=json_path,
        json_sha256=json_sha,
        markdown_path=markdown_path,
        markdown_sha256=markdown_sha,
        model_id=agent_out.response.model_id,
        tokens_in=agent_out.response.tokens_in,
        tokens_out=agent_out.response.tokens_out,
    )

    # ── Attach evidence to bundle and persist ────────────────────────────────
    bundle.explanation = evidence
    to_db(bundle, session)

    # ── Advance case status ──────────────────────────────────────────────────
    case_row = RepairCaseRepo(session).get_by_id(case_id)
    RepairCaseRepo(session).set_status(case_row, "EXPLAINED")
    bundle.meta.status = "EXPLAINED"

    log.info(
        "Case %s explanation complete: json=%s md=%s tokens=%d+%d",
        case_id[:8], json_path, markdown_path,
        agent_out.response.tokens_in, agent_out.response.tokens_out,
    )

    return ExplainResult(
        case_id=case_id,
        json_path=json_path,
        markdown_path=markdown_path,
        model_id=agent_out.response.model_id,
        tokens_in=agent_out.response.tokens_in,
        tokens_out=agent_out.response.tokens_out,
    )


def _next_agent_call_index(case_id: str, session: Session) -> int:
    """Return the next sequential call index for ExplanationAgent logs.

    Uses per-agent-type counting to be consistent with LocalizationAgent and
    RepairAgent (which also count only their own prior calls, not global ones).
    """
    from sqlalchemy import select, func
    from ..storage.models import AgentLog

    stmt = select(func.count()).where(
        AgentLog.repair_case_id == case_id,
        AgentLog.agent_type == AGENT_TYPE,
    )
    count = session.scalar(stmt) or 0
    return count
