"""Static analysis orchestrator — Phase 2 / Stage 2-3 of the thesis pipeline."""

from __future__ import annotations

from pathlib import Path

from ..domain.analysis import ImpactGraph
from ..utils.log import get_logger
from .dependency_graph import DependencyGraph
from .expect_actual import ExpectActualResolver
from .kotlin_parser import parse_project
from .symbol_table import SymbolTable

log = get_logger(__name__)


def run_static_analysis(
    project_dir: str | Path,
    dependency_group: str,
    version_before: str,
    version_after: str,
) -> ImpactGraph:
    """Parse Kotlin sources, build the import graph, propagate impact via BFS.

    This is a direct port of the prototype's run_static_analysis with the
    interface updated to match the thesis pipeline (no ShadowManifest dependency).
    """
    root = Path(project_dir)
    log.info(f"Parsing Kotlin files in {root}...")
    parse_results = parse_project(root)
    log.info(f"Parsed {len(parse_results)} Kotlin files")

    symbol_table = SymbolTable()
    symbol_table.build(parse_results)

    ea_resolver = ExpectActualResolver()
    ea_resolver.build(parse_results)

    graph = DependencyGraph()
    seeds = graph.build(parse_results, dependency_group)

    if not seeds:
        log.warning(f"No files found importing '{dependency_group}'")

    impacted = graph.propagate_impact(seeds, ea_resolver, str(root))

    result = ImpactGraph(
        dependency_group=dependency_group,
        version_before=version_before,
        version_after=version_after,
        seed_files=seeds,
        impacted_files=impacted,
        expect_actual_pairs=ea_resolver.pairs,
        total_project_files=len(parse_results),
        total_impacted=len(impacted),
    )

    log.info(
        f"[bold green]Static analysis complete[/bold green]: "
        f"{result.total_impacted}/{result.total_project_files} files impacted "
        f"({len(seeds)} direct, {len(ea_resolver.pairs)} expect/actual pairs)"
    )
    return result
