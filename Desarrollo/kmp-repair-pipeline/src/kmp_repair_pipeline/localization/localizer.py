"""Phase 8 orchestrator — hybrid impact localization.

Flow:
  1. Rehydrate CaseBundle from DB (must be ANALYZED)
  2. Deterministic scoring (scoring.py) → ranked ScoredCandidate list
  3. LocalizationAgent LLM call  (skipped when use_agent=False)
  4. Merge: agent output wins when available; deterministic is fallback
  5. Persist localization_candidates rows to DB
  6. Log agent call to agent_logs table
  7. Write prompt + response to ArtifactStore
  8. Advance bundle status → LOCALIZED

The caller controls the session commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.evidence import LocalizationResult
from ..case_bundle.serialization import from_db_case, to_db
from ..domain.analysis import ImpactGraph
from ..localization.localization_agent import (
    AGENT_TYPE,
    _deterministic_to_result_candidates,
    run_localization_agent,
)
from ..localization.scoring import ScoredCandidate, score_candidates
from ..static_analysis.analyzer import run_static_analysis
from ..storage.artifact_store import ArtifactStore
from ..storage.repositories import (
    AgentLogRepo,
    LocalizationCandidateRepo,
    RevisionRepo,
    RepairCaseRepo,
)
from ..utils.llm_provider import LLMProvider, get_default_provider
from ..utils.log import get_logger

log = get_logger(__name__)


@dataclass
class LocalizationRunResult:
    bundle: CaseBundle
    deterministic_candidates: list[ScoredCandidate]
    used_agent: bool
    agent_notes: str = ""
    total_candidates: int = 0


def localize(
    case_id: str,
    session: Session,
    artifact_base: Path | str = Path("data/artifacts"),
    use_agent: bool = True,
    provider: Optional[LLMProvider] = None,
    top_k: int = 10,
) -> LocalizationRunResult:
    """Run hybrid impact localization for `case_id`.

    Parameters
    ----------
    case_id:
        UUID of a repair case that has been analyzed (status ANALYZED or EXECUTED).
    session:
        Active SQLAlchemy session (caller controls commit).
    artifact_base:
        Root of the artifact store.
    use_agent:
        Whether to call the LocalizationAgent. False → deterministic only.
    provider:
        LLM provider override. None → get_default_provider().
    top_k:
        Maximum candidates to persist and return.
    """
    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    # Gather evidence
    impact_graph, direct_imports = _resolve_static_signals(
        bundle=bundle,
        case_id=case_id,
        session=session,
    )
    error_obs = bundle.execution.all_errors("after") if bundle.execution else []

    # --- Step 1: Deterministic scoring ------------------------------------
    scored = score_candidates(
        impact_graph=impact_graph,
        structural=bundle.structural,
        error_observations=error_obs,
        direct_import_files=direct_imports,
    )
    log.info("Deterministic scoring: %d candidates", len(scored))

    # --- Step 2: LocalizationAgent (optional) ----------------------------
    result_candidates: list[LocalizationResult.Candidate]
    used_agent = False
    agent_notes = ""

    if use_agent and scored:
        llm = provider or get_default_provider()
        artifact_store = ArtifactStore(artifact_base, case_id)
        call_index = _next_agent_call_index(case_id, session)

        try:
            agent_out = run_localization_agent(
                localization_context=bundle.localization_context(),
                deterministic_candidates=scored,
                provider=llm,
            )
            result_candidates = agent_out.candidates
            agent_notes = agent_out.agent_notes
            used_agent = True

            # Persist prompt + response artifacts
            prompt_path, prompt_sha = artifact_store.write_prompt(
                AGENT_TYPE, call_index, agent_out.prompt
            )
            response_path, response_sha = artifact_store.write_response(
                AGENT_TYPE, call_index, agent_out.response.content
            )

            # Log agent call to DB
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

        except Exception as exc:
            log.warning("LocalizationAgent call failed (%s) — using deterministic fallback", exc)
            AgentLogRepo(session).create(
                repair_case_id=case_id,
                agent_type=AGENT_TYPE,
                call_index=call_index,
                error=str(exc)[:500],
            )
            result_candidates = _deterministic_to_result_candidates(scored)
    else:
        if use_agent and not scored:
            log.warning("No candidates from scoring — skipping agent call")
        result_candidates = _deterministic_to_result_candidates(scored)

    # Trim to top_k
    result_candidates = result_candidates[:top_k]

    # --- Step 3: Persist localization_candidates --------------------------
    cand_repo = LocalizationCandidateRepo(session)
    for cand in result_candidates:
        cand_repo.create(
            repair_case_id=case_id,
            rank=cand.rank,
            score=cand.score,
            classification=cand.classification,
            file_path=cand.file_path,
            source_set=cand.source_set,
            score_breakdown=cand.score_breakdown,
        )

    # --- Step 4: Update bundle -------------------------------------------
    loc_result = LocalizationResult(candidates=result_candidates)
    bundle.set_localization_result(loc_result)
    to_db(bundle, session)

    RepairCaseRepo(session).set_status(
        RepairCaseRepo(session).get_by_id(case_id), "LOCALIZED"
    )

    log.info(
        "Case %s localized: %d candidates (agent=%s)",
        case_id[:8], len(result_candidates), used_agent,
    )

    return LocalizationRunResult(
        bundle=bundle,
        deterministic_candidates=scored,
        used_agent=used_agent,
        agent_notes=agent_notes,
        total_candidates=len(result_candidates),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_agent_call_index(case_id: str, session: Session) -> int:
    """Return the next sequential call index for agent logs."""
    from sqlalchemy import select, func
    from ..storage.models import AgentLog

    stmt = select(func.count()).where(
        AgentLog.repair_case_id == case_id,
        AgentLog.agent_type == AGENT_TYPE,
    )
    count = session.scalar(stmt) or 0
    return count


def _resolve_static_signals(
    bundle: CaseBundle,
    case_id: str,
    session: Session,
) -> tuple[ImpactGraph | None, list[str]]:
    """Return (impact_graph, direct_import_files) for deterministic localization.

    Rehydrated bundles may not carry an ImpactGraph because that object is not
    normalized in DB tables. In that case, rebuild it from the after-clone and
    update evidence to preserve the thesis static-signal pipeline.
    """
    if bundle.structural and bundle.structural.impact_graph is not None:
        return bundle.structural.impact_graph, bundle.structural.direct_import_files

    if not bundle.update_evidence or not bundle.update_evidence.version_changes:
        return None, bundle.structural.direct_import_files if bundle.structural else []

    after_rev = RevisionRepo(session).get(case_id, "after")
    if after_rev is None or not after_rev.local_path:
        return None, bundle.structural.direct_import_files if bundle.structural else []

    graphs: list[ImpactGraph] = []
    for vc in bundle.update_evidence.version_changes:
        try:
            graphs.append(
                run_static_analysis(
                    project_dir=after_rev.local_path,
                    dependency_group=vc.dependency_group,
                    version_before=vc.before,
                    version_after=vc.after,
                )
            )
        except Exception as exc:
            log.warning(
                "Case %s: static re-analysis failed for %s: %s",
                case_id[:8], vc.dependency_group, exc,
            )

    merged = _merge_graphs(graphs)
    direct_imports: list[str] = []
    for g in graphs:
        for sf in g.seed_files:
            if sf not in direct_imports:
                direct_imports.append(sf)

    if bundle.structural:
        bundle.structural.impact_graph = merged
        if direct_imports:
            bundle.structural.direct_import_files = direct_imports

    return merged, direct_imports


def _merge_graphs(graphs: list[ImpactGraph]) -> ImpactGraph | None:
    """Merge multiple dependency impact graphs into one union graph."""
    if not graphs:
        return None
    if len(graphs) == 1:
        return graphs[0]

    base = graphs[0]
    seen_paths = {fi.file_path for fi in base.impacted_files}
    merged_impacted = list(base.impacted_files)
    merged_seeds = list(base.seed_files)
    merged_pairs = list(base.expect_actual_pairs)
    seen_fqcns = {p.expect_fqcn for p in merged_pairs}

    for g in graphs[1:]:
        for fi in g.impacted_files:
            if fi.file_path not in seen_paths:
                seen_paths.add(fi.file_path)
                merged_impacted.append(fi)
        for sf in g.seed_files:
            if sf not in merged_seeds:
                merged_seeds.append(sf)
        for pair in g.expect_actual_pairs:
            if pair.expect_fqcn not in seen_fqcns:
                seen_fqcns.add(pair.expect_fqcn)
                merged_pairs.append(pair)

    return ImpactGraph(
        dependency_group=", ".join(g.dependency_group for g in graphs),
        version_before=graphs[0].version_before,
        version_after=graphs[0].version_after,
        seed_files=merged_seeds,
        impacted_files=merged_impacted,
        expect_actual_pairs=merged_pairs,
        total_project_files=base.total_project_files,
        total_impacted=len(merged_impacted),
    )
