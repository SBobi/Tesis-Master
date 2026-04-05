"""Build StructuralEvidence from a cloned KMP repository.

Phase 7 orchestrator:

  1. Rehydrate CaseBundle from DB
  2. Locate after-clone path from revisions table
  3. Run KMP-aware static analysis (parse_project → SymbolTable → DependencyGraph → BFS)
     for each changed dependency group in UpdateEvidence
  4. Build StructuralEvidence (SourceSetMap, ImpactGraph, expect/actual pairs,
     direct import files, build file list)
  5. Persist source_entities and expect_actual_links to DB
  6. Attach StructuralEvidence to CaseBundle → status ANALYZED
  7. Return updated bundle

The caller (CLI or orchestrator) controls the session commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.evidence import SourceSetMap, StructuralEvidence
from ..case_bundle.serialization import from_db_case, to_db
from ..domain.analysis import ExpectActualPair, ImpactGraph
from ..storage.models import ExpectActualLink
from ..storage.repositories import RepairCaseRepo, RevisionRepo, SourceEntityRepo
from ..utils.log import get_logger
from .analyzer import run_static_analysis
from .kotlin_parser import parse_project

log = get_logger(__name__)


@dataclass
class StructuralAnalysisResult:
    bundle: CaseBundle
    impact_graphs: list[ImpactGraph]   # one per changed dependency group
    total_kotlin_files: int
    total_impacted_files: int


def analyze_case(
    case_id: str,
    session: Session,
) -> StructuralAnalysisResult:
    """Run KMP structural analysis for `case_id` and persist the evidence.

    Parameters
    ----------
    case_id:
        UUID of a repair case that has been built (status SHADOW_BUILT or EXECUTED).
    session:
        Active SQLAlchemy session (caller controls commit).
    """
    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    # Locate the after-clone
    rev_repo = RevisionRepo(session)
    after_rev = rev_repo.get(case_id, "after")
    if after_rev is None or not after_rev.local_path:
        raise ValueError(
            f"Case {case_id}: after revision not cloned — run `build-case` first"
        )

    after_path = Path(after_rev.local_path)
    log.info("Analyzing repo at %s", after_path)

    # Determine which dependency groups to analyse
    version_changes = (
        bundle.update_evidence.version_changes
        if bundle.update_evidence
        else []
    )
    if not version_changes:
        raise ValueError(f"Case {case_id}: no version changes in update evidence")

    # --- Full project parse (once) ----------------------------------------
    log.info("Parsing Kotlin sources...")
    parse_results = parse_project(after_path)
    total_kotlin_files = len(parse_results)
    log.info("Parsed %d Kotlin files", total_kotlin_files)

    # --- Build SourceSetMap from parse results ----------------------------
    source_set_map = _build_source_set_map(parse_results)

    # --- Run analysis per dependency group --------------------------------
    impact_graphs: list[ImpactGraph] = []
    all_direct_import_files: set[str] = set()

    for vc in version_changes:
        log.info(
            "Analysing impact of %s (%s → %s)",
            vc.dependency_group, vc.before, vc.after,
        )
        graph = run_static_analysis(
            project_dir=after_path,
            dependency_group=vc.dependency_group,
            version_before=vc.before,
            version_after=vc.after,
        )
        impact_graphs.append(graph)
        all_direct_import_files.update(graph.seed_files)

    # Merge impact graphs into a single representative view
    merged = _merge_graphs(impact_graphs)

    # --- Collect expect/actual pairs (union across all groups) -----------
    all_pairs: list[ExpectActualPair] = []
    seen_fqcns: set[str] = set()
    for g in impact_graphs:
        for pair in g.expect_actual_pairs:
            if pair.expect_fqcn not in seen_fqcns:
                seen_fqcns.add(pair.expect_fqcn)
                all_pairs.append(pair)

    # --- Build file lists -------------------------------------------------
    relevant_build_files = _find_build_files(after_path)

    # --- Parse version catalog -------------------------------------------
    version_catalog = _parse_version_catalog(after_path)
    if version_catalog:
        log.info(
            "Case %s: version catalog has %d entries (kotlin=%s)",
            case_id[:8], len(version_catalog),
            version_catalog.get("kotlin", "not found"),
        )

    total_impacted = len(merged.impacted_files) if merged else 0

    # --- Persist to DB ---------------------------------------------------
    _persist_source_entities(case_id, parse_results, session)
    if all_pairs:
        _persist_expect_actual_links(case_id, all_pairs, session)

    # --- Attach to bundle ------------------------------------------------
    structural_ev = StructuralEvidence(
        impact_graph=merged,
        source_set_map=source_set_map,
        expect_actual_pairs=all_pairs,
        direct_import_files=list(all_direct_import_files),
        relevant_build_files=relevant_build_files,
        version_catalog=version_catalog,
        total_kotlin_files=total_kotlin_files,
    )

    bundle.set_structural_evidence(structural_ev)
    to_db(bundle, session)

    RepairCaseRepo(session).set_status(
        RepairCaseRepo(session).get_by_id(case_id), "ANALYZED"
    )

    log.info(
        "Case %s structural analysis complete: %d files parsed, %d impacted, %d expect/actual pairs",
        case_id[:8], total_kotlin_files, total_impacted, len(all_pairs),
    )

    return StructuralAnalysisResult(
        bundle=bundle,
        impact_graphs=impact_graphs,
        total_kotlin_files=total_kotlin_files,
        total_impacted_files=total_impacted,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_source_set_map(parse_results) -> SourceSetMap:
    """Group file paths by source set."""
    buckets: dict[str, list[str]] = {}
    for pr in parse_results:
        ss = pr.source_set
        buckets.setdefault(ss, []).append(pr.file_path)

    return SourceSetMap(
        common_files=buckets.pop("common", []),
        android_files=buckets.pop("android", []),
        ios_files=buckets.pop("ios", []),
        jvm_files=buckets.pop("jvm", []),
        other=buckets,
    )


def _merge_graphs(graphs: list[ImpactGraph]) -> ImpactGraph | None:
    """Merge multiple dependency impact graphs into one (union of impacted files)."""
    if not graphs:
        return None
    if len(graphs) == 1:
        return graphs[0]

    # Use the first as base; union impacted files from rest
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


def _parse_version_catalog(repo: Path) -> dict[str, str]:
    """Parse gradle/libs.versions.toml and return the [versions] section.

    Returns a dict of alias → version string (e.g. {"kotlin": "2.2.0", ...}).
    Returns an empty dict when no version catalog is found or parsing fails.

    This is a lightweight TOML parser for the [versions] section only — it
    handles the common `key = "value"` format without a full TOML dependency.
    """
    candidates = [
        repo / "gradle" / "libs.versions.toml",
        repo / "libs.versions.toml",
    ]
    toml_path: Path | None = None
    for c in candidates:
        if c.exists():
            toml_path = c
            break

    if toml_path is None:
        return {}

    try:
        text = toml_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    versions: dict[str, str] = {}
    in_versions_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_versions_section = stripped.startswith("[versions]")
            continue
        if not in_versions_section:
            continue
        if "=" in stripped and not stripped.startswith("#"):
            key, _, val = stripped.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val:
                versions[key] = val

    return versions


def _find_build_files(repo: Path) -> list[str]:
    """Collect Gradle build files relevant to KMP configuration."""
    patterns = [
        "build.gradle.kts",
        "build.gradle",
        "settings.gradle.kts",
        "settings.gradle",
        "gradle/libs.versions.toml",
        "libs.versions.toml",
        "gradle/wrapper/gradle-wrapper.properties",
    ]
    found = []
    for pattern in patterns:
        p = repo / pattern
        if p.exists():
            found.append(str(p.relative_to(repo)))
    return found


def _persist_source_entities(case_id: str, parse_results, session: Session) -> None:
    """Insert one source_entity row per unique file (first declaration wins)."""
    repo = SourceEntityRepo(session)

    # Group declarations by file; we store one row per file (file-level granularity)
    seen_files: set[str] = set()
    for pr in parse_results:
        if pr.file_path in seen_files:
            continue
        seen_files.add(pr.file_path)

        # Pick the primary declaration kind for this file (first one found)
        decl_kind = pr.declarations[0].kind.value if pr.declarations else None
        fqcn = pr.declarations[0].fqcn if pr.declarations else None
        has_expect = any(d.is_expect for d in pr.declarations)
        has_actual = any(d.is_actual for d in pr.declarations)

        repo.create(
            repair_case_id=case_id,
            file_path=pr.file_path,
            source_set=pr.source_set,
            package=pr.package or None,
            declaration_kind=decl_kind,
            fqcn=fqcn,
            is_expect=has_expect,
            is_actual=has_actual,
        )

    log.debug("Persisted %d source_entity rows for case %s", len(seen_files), case_id[:8])


def _persist_expect_actual_links(
    case_id: str,
    pairs: list[ExpectActualPair],
    session: Session,
) -> None:
    """Insert expect_actual_link rows for resolved pairs.

    We look up the source_entity rows by file_path to get their IDs.
    Pairs where the expect or actual file is missing from source_entities are skipped.
    """
    from sqlalchemy import select
    from ..storage.models import SourceEntity

    # Build a file_path → entity_id index from what we just inserted
    stmt = select(SourceEntity).where(SourceEntity.repair_case_id == case_id)
    entities = {e.file_path: e for e in session.scalars(stmt).all()}

    inserted = 0
    for pair in pairs:
        expect_entity = entities.get(pair.expect_file)
        if expect_entity is None:
            log.debug("expect_actual_link: expect file not in source_entities: %s", pair.expect_file)
            continue

        for actual_file in pair.actual_files:
            actual_entity = entities.get(actual_file)
            if actual_entity is None:
                log.debug("expect_actual_link: actual file not in source_entities: %s", actual_file)
                continue

            link = ExpectActualLink(
                repair_case_id=case_id,
                expect_entity_id=expect_entity.id,
                actual_entity_id=actual_entity.id,
                fqcn=pair.expect_fqcn,
            )
            session.add(link)
            inserted += 1

    session.flush()
    log.debug("Persisted %d expect_actual_link rows for case %s", inserted, case_id[:8])
