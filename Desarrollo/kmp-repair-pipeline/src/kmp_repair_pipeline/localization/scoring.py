"""Deterministic localization scorer — Stage 3 static + dynamic scoring.

Produces a scored candidate list from structural and execution evidence
without calling an LLM. Used as the input to LocalizationAgent and as a
standalone baseline.

Scoring formula
---------------
  static_score  = base_score(impact_relation, bfs_distance)
                + expect_actual_bonus
                + direct_import_bonus
  dynamic_score = error_mention_score(error_observations)
  final_score   = 0.6 * static_score + 0.4 * dynamic_score

All scores are in [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..case_bundle.evidence import ErrorObservation, StructuralEvidence
from ..domain.analysis import FileImpact, ImpactGraph, ImpactRelation
from ..utils.log import get_logger

log = get_logger(__name__)

# Weight parameters
_STATIC_WEIGHT = 0.6
_DYNAMIC_WEIGHT = 0.4

# Static base scores by relation type
_BASE_SCORE: dict[str, float] = {
    ImpactRelation.DIRECT.value: 1.0,
    ImpactRelation.EXPECT_ACTUAL.value: 0.85,
    ImpactRelation.TRANSITIVE.value: 0.5,
}

# BFS distance decay
_DISTANCE_DECAY = 0.15   # subtract per hop beyond 1

# Bonus scores
_EXPECT_ACTUAL_BONUS = 0.15
_DIRECT_IMPORT_BONUS = 0.10

# Dynamic: per-error-mention bonus (capped)
_ERROR_MENTION_SCORE_PER_HIT = 0.25
_ERROR_MENTION_CAP = 1.0


@dataclass
class ScoredCandidate:
    file_path: str
    source_set: str
    static_score: float
    dynamic_score: float
    final_score: float
    classification: str        # "shared_code" | "platform_specific" | "build_level" | "uncertain"
    score_breakdown: dict = field(default_factory=dict)


def score_candidates(
    impact_graph: ImpactGraph | None,
    structural: StructuralEvidence | None,
    error_observations: list[ErrorObservation],
    direct_import_files: list[str] | None = None,
) -> list[ScoredCandidate]:
    """Produce a ranked list of localization candidates.

    Parameters
    ----------
    impact_graph:
        BFS impact graph from structural analysis (may be None for empty repos).
    structural:
        StructuralEvidence for source-set attribution.
    error_observations:
        ErrorObservations from the after-revision execution.
    direct_import_files:
        Files that directly import from the changed dependency (seeds).
    """
    if impact_graph is None:
        log.warning("No impact graph available — scoring on error observations only")
        return _score_from_errors_only(error_observations, structural)

    direct_imports = set(direct_import_files or impact_graph.seed_files)
    expect_actual_files = _collect_expect_actual_files(impact_graph)
    error_file_counts = _count_error_mentions(error_observations)

    candidates: dict[str, ScoredCandidate] = {}

    for fi in impact_graph.impacted_files:
        path = fi.file_path
        static_s = _compute_static_score(fi, direct_imports, expect_actual_files)
        dynamic_s = _compute_dynamic_score(path, error_file_counts)
        final_s = _STATIC_WEIGHT * static_s + _DYNAMIC_WEIGHT * dynamic_s

        source_set = _resolve_source_set(path, structural)
        classification = _classify(source_set, fi, direct_imports)

        candidates[path] = ScoredCandidate(
            file_path=path,
            source_set=source_set,
            static_score=round(static_s, 4),
            dynamic_score=round(dynamic_s, 4),
            final_score=round(final_s, 4),
            classification=classification,
            score_breakdown={
                "static": round(static_s, 4),
                "dynamic": round(dynamic_s, 4),
                "relation": fi.relation.value,
                "distance": fi.distance,
                "error_count": error_file_counts.get(_basename(path), 0),
            },
        )

    # Add error-mentioned files that weren't in the impact graph
    for basename, count in error_file_counts.items():
        existing = _find_by_basename(candidates, basename)
        if existing is None:
            dynamic_s = min(count * _ERROR_MENTION_SCORE_PER_HIT, _ERROR_MENTION_CAP)
            final_s = _DYNAMIC_WEIGHT * dynamic_s
            # Try to resolve the full path from error observations
            full_path = _full_path_from_errors(basename, error_observations)
            source_set = _resolve_source_set(full_path or basename, structural)
            candidates[full_path or basename] = ScoredCandidate(
                file_path=full_path or basename,
                source_set=source_set,
                static_score=0.0,
                dynamic_score=round(dynamic_s, 4),
                final_score=round(final_s, 4),
                classification="uncertain",
                score_breakdown={
                    "static": 0.0,
                    "dynamic": round(dynamic_s, 4),
                    "relation": "error_only",
                    "distance": -1,
                    "error_count": count,
                },
            )

    ranked = sorted(candidates.values(), key=lambda c: c.final_score, reverse=True)
    log.info(
        "Scored %d candidates (top score=%.3f)",
        len(ranked),
        ranked[0].final_score if ranked else 0.0,
    )
    return ranked


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_static_score(
    fi: FileImpact,
    direct_imports: set[str],
    expect_actual_files: set[str],
) -> float:
    base = _BASE_SCORE.get(fi.relation.value, 0.3)

    # Distance decay beyond 1 hop
    if fi.distance > 1:
        base -= (fi.distance - 1) * _DISTANCE_DECAY
    base = max(base, 0.0)

    # Bonuses
    if fi.file_path in expect_actual_files:
        base = min(base + _EXPECT_ACTUAL_BONUS, 1.0)
    if fi.file_path in direct_imports:
        base = min(base + _DIRECT_IMPORT_BONUS, 1.0)

    return min(base, 1.0)


def _compute_dynamic_score(file_path: str, error_counts: dict[str, int]) -> float:
    count = error_counts.get(_basename(file_path), 0)
    return min(count * _ERROR_MENTION_SCORE_PER_HIT, _ERROR_MENTION_CAP)


def _count_error_mentions(errors: list[ErrorObservation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in errors:
        if e.file_path:
            key = _basename(e.file_path)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _collect_expect_actual_files(graph: ImpactGraph) -> set[str]:
    files: set[str] = set()
    for pair in graph.expect_actual_pairs:
        files.add(pair.expect_file)
        files.update(pair.actual_files)
    return files


def _resolve_source_set(file_path: str, structural: StructuralEvidence | None) -> str:
    if structural is None:
        return "unknown"
    return structural.source_set_map.source_set_for(file_path)


def _classify(source_set: str, fi: FileImpact, direct_imports: set[str]) -> str:
    if source_set == "common":
        return "shared_code"
    if source_set in ("android", "ios", "jvm", "native"):
        return "platform_specific"
    if fi.file_path in direct_imports and source_set not in ("common",):
        return "build_level"
    return "uncertain"


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _find_by_basename(candidates: dict[str, ScoredCandidate], basename: str) -> ScoredCandidate | None:
    for path, cand in candidates.items():
        if _basename(path) == basename:
            return cand
    return None


def _full_path_from_errors(basename: str, errors: list[ErrorObservation]) -> str | None:
    for e in errors:
        if e.file_path and _basename(e.file_path) == basename:
            return e.file_path
    return None


def _score_from_errors_only(
    errors: list[ErrorObservation],
    structural: StructuralEvidence | None,
) -> list[ScoredCandidate]:
    """Fallback: build candidates solely from error observations."""
    counts = _count_error_mentions(errors)
    candidates = []
    for basename, count in sorted(counts.items(), key=lambda x: -x[1]):
        dynamic_s = min(count * _ERROR_MENTION_SCORE_PER_HIT, _ERROR_MENTION_CAP)
        full_path = _full_path_from_errors(basename, errors) or basename
        ss = _resolve_source_set(full_path, structural)
        candidates.append(ScoredCandidate(
            file_path=full_path,
            source_set=ss,
            static_score=0.0,
            dynamic_score=round(dynamic_s, 4),
            final_score=round(_DYNAMIC_WEIGHT * dynamic_s, 4),
            classification="uncertain",
            score_breakdown={"static": 0.0, "dynamic": round(dynamic_s, 4), "error_count": count},
        ))
    return candidates
