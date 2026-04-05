"""Per-case thesis metrics — pure functions, no I/O, no DB.

Metric definitions
------------------
BSR  — Build Success Rate
       1.0 if the validated patch produced no build failures across all
       runnable targets; 0.0 otherwise.

CTSR — Compile-Time Success Rate
       1.0 if no FAILED_BUILD status appears in validation target results;
       0.0 otherwise. (BSR ≥ CTSR by construction because a FAILED_TESTS
       case passes the compile check but fails at test time.)

FFSR — Full Fix Success Rate
       1.0 if ALL runnable targets report SUCCESS_REPOSITORY_LEVEL;
       0.0 otherwise. (Identical to BSR for the subset without test runs,
       but distinguishes partial fixes when tests are run.)

EFR  — Error Fix Rate
       Fraction of original compiler errors (from execution.after) that do
       NOT appear in the validation run output.
       EFR = 1 - |remaining_errors| / |original_errors|
       Returns None if original_errors is empty (division guard).

Hit@k — Localization quality
        1.0 if any ground-truth changed file appears in the top-k
        localization candidates; 0.0 otherwise.
        Returns None if ground_truth_files is empty.

source_set_accuracy
        Fraction of candidates whose `source_set` matches a ground-truth
        mapping. Returns None if ground_truth_map is empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..case_bundle.evidence import (
    ErrorObservation,
    TargetValidation,
    ValidationEvidence,
)
from ..domain.validation import ValidationStatus


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class CaseMetrics:
    """All thesis metrics for one (case, repair_mode) pair."""
    case_id: str
    repair_mode: str
    bsr: float
    ctsr: float
    ffsr: float
    efr: Optional[float]             # None when no original errors exist
    efr_normalized: Optional[float]  # EFR with message-only dedup key (no line number)
    hit_at_1: Optional[float]
    hit_at_3: Optional[float]
    hit_at_5: Optional[float]
    source_set_accuracy: Optional[float]  # None when no ground truth
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual metric functions
# ---------------------------------------------------------------------------


def compute_bsr(validation: Optional[ValidationEvidence]) -> float:
    """1.0 if overall validation status is SUCCESS_REPOSITORY_LEVEL."""
    if validation is None:
        return 0.0
    return 1.0 if validation.repository_level_status == ValidationStatus.SUCCESS_REPOSITORY_LEVEL else 0.0


def compute_ctsr(validation: Optional[ValidationEvidence]) -> float:
    """1.0 if no runnable target has FAILED_BUILD."""
    if validation is None:
        return 0.0
    for r in validation.target_results:
        if r.status == ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE:
            continue
        if r.status == ValidationStatus.FAILED_BUILD:
            return 0.0
    return 1.0


def compute_ffsr(validation: Optional[ValidationEvidence]) -> float:
    """1.0 if every runnable target achieved SUCCESS_REPOSITORY_LEVEL."""
    if validation is None:
        return 0.0
    runnable = [
        r for r in validation.target_results
        if r.status != ValidationStatus.NOT_RUN_ENVIRONMENT_UNAVAILABLE
    ]
    if not runnable:
        return 0.0
    return 1.0 if all(r.status == ValidationStatus.SUCCESS_REPOSITORY_LEVEL for r in runnable) else 0.0


def compute_efr(
    original_errors: list[ErrorObservation],
    remaining_errors: list[ErrorObservation],
) -> Optional[float]:
    """Fraction of original errors eliminated.

    Uses a penalty-adjusted formula so that patches which replace N original
    errors with M > N new errors do not score EFR=1.0 (all "fixed") when the
    build is clearly more broken than before.

    Formula:
      raw_efr   = (|original| - |original ∩ remaining|) / |original|
      new_errors = max(0, |remaining| - |original|)
      penalty   = new_errors / |original|
      EFR       = max(0, raw_efr - penalty)

    This ensures that introducing more errors than were fixed pushes EFR to 0
    even when the original specific error keys no longer appear verbatim (e.g.
    because a wrong version bump changed KLIB errors into classpath errors).

    Returns None when there are no original errors.
    """
    if not original_errors:
        return None
    original_keys = {_error_key(e) for e in original_errors}
    remaining_keys = {_error_key(e) for e in remaining_errors}
    fixed = original_keys - remaining_keys
    raw_efr = len(fixed) / len(original_keys)

    # Penalty for introducing new errors beyond what was fixed
    new_error_count = max(0, len(remaining_errors) - len(original_errors))
    penalty = new_error_count / len(original_errors)

    return round(max(0.0, raw_efr - penalty), 4)


def compute_hit_at_k(
    candidates: list[str],
    ground_truth_files: list[str],
    k: int,
) -> Optional[float]:
    """1.0 if any ground-truth file appears in candidates[:k].

    Returns None when ground_truth_files is empty.
    """
    if not ground_truth_files:
        return None
    top_k = set(candidates[:k])
    gt_set = set(ground_truth_files)
    return 1.0 if top_k & gt_set else 0.0


def compute_source_set_accuracy(
    candidates: list[dict],
    ground_truth_source_sets: dict[str, str],
) -> Optional[float]:
    """Fraction of candidates with the correct source_set label.

    Parameters
    ----------
    candidates:
        List of dicts with keys ``file_path`` and ``source_set``.
    ground_truth_source_sets:
        Mapping of file_path → correct source_set label.

    Returns None when ground_truth_source_sets is empty.
    """
    if not ground_truth_source_sets:
        return None
    matched = 0
    total = 0
    for c in candidates:
        fp = c.get("file_path", "")
        if fp in ground_truth_source_sets:
            total += 1
            if c.get("source_set") == ground_truth_source_sets[fp]:
                matched += 1
    return round(matched / total, 4) if total > 0 else None


# ---------------------------------------------------------------------------
# Top-level compute function
# ---------------------------------------------------------------------------


def compute_metrics(
    case_id: str,
    repair_mode: str,
    validation: Optional[ValidationEvidence],
    original_errors: list[ErrorObservation],
    remaining_errors: list[ErrorObservation],
    localization_candidates: list[dict],
    ground_truth_files: Optional[list[str]] = None,
    ground_truth_source_sets: Optional[dict[str, str]] = None,
) -> CaseMetrics:
    """Compute all thesis metrics for one (case, repair_mode) pair.

    Parameters
    ----------
    case_id, repair_mode:
        Identifiers for the DB upsert key.
    validation:
        ValidationEvidence from the bundle (may be None).
    original_errors:
        Compiler errors from `execution.after.error_observations`.
    remaining_errors:
        Compiler errors from validation run outputs (post-patch).
    localization_candidates:
        Ordered list of dicts with at least ``file_path`` and ``source_set``.
    ground_truth_files:
        Known-changed files for Hit@k.  Pass empty list / None to skip.
    ground_truth_source_sets:
        Dict of file_path → source_set for attribution accuracy.  Optional.
    """
    candidate_paths = [c.get("file_path", "") for c in localization_candidates]

    return CaseMetrics(
        case_id=case_id,
        repair_mode=repair_mode,
        bsr=compute_bsr(validation),
        ctsr=compute_ctsr(validation),
        ffsr=compute_ffsr(validation),
        efr=compute_efr(original_errors, remaining_errors),
        efr_normalized=compute_efr_message_normalized(original_errors, remaining_errors),
        hit_at_1=compute_hit_at_k(candidate_paths, ground_truth_files or [], 1),
        hit_at_3=compute_hit_at_k(candidate_paths, ground_truth_files or [], 3),
        hit_at_5=compute_hit_at_k(candidate_paths, ground_truth_files or [], 5),
        source_set_accuracy=compute_source_set_accuracy(
            localization_candidates, ground_truth_source_sets or {}
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _error_key(e: ErrorObservation) -> tuple:
    """Deduplication key: (type, file, line, message).

    Includes the line number so that two identical messages on different lines
    are counted as different errors.  This can *overcount* fixed errors when a
    patch moves an error to a different line without actually eliminating it.
    Use ``_error_key_normalized`` for a line-agnostic alternative.
    """
    return (
        getattr(e, "error_type", ""),
        getattr(e, "file_path", ""),
        getattr(e, "line", None),
        getattr(e, "message", ""),
    )


def _error_key_normalized(e: ErrorObservation) -> tuple:
    """Message-normalized dedup key: (type, file, message) — no line number.

    Prevents the line-number shift false-positive: if a patch moves an error
    from line 42 to line 45 but does not fix it, ``_error_key`` counts it as
    fixed while ``_error_key_normalized`` does not.  Gives a more conservative
    (lower-bound) EFR estimate that is harder to game with cosmetic changes.
    """
    return (
        getattr(e, "error_type", ""),
        getattr(e, "file_path", ""),
        getattr(e, "message", ""),
    )


def compute_efr_message_normalized(
    original_errors: list[ErrorObservation],
    remaining_errors: list[ErrorObservation],
) -> Optional[float]:
    """EFR variant using message-only dedup key (no line number).

    Otherwise identical in formula to ``compute_efr``:
      EFR_normalized = max(0, raw_efr_norm - penalty)

    Returns None when there are no original errors.
    """
    if not original_errors:
        return None
    original_keys = {_error_key_normalized(e) for e in original_errors}
    remaining_keys = {_error_key_normalized(e) for e in remaining_errors}
    fixed = original_keys - remaining_keys
    raw_efr = len(fixed) / len(original_keys)

    new_error_count = max(0, len(remaining_errors) - len(original_errors))
    penalty = new_error_count / len(original_errors)

    return round(max(0.0, raw_efr - penalty), 4)
