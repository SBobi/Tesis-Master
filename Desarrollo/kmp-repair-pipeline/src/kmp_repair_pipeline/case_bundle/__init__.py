"""Canonical typed Case Bundle — the primary runtime state of one repair case."""

from .bundle import CaseBundle, CaseMeta
from .evidence import (
    ErrorObservation,
    ExecutionEvidence,
    ExplanationEvidence,
    LocalizationResult,
    PatchAttempt,
    RepairEvidence,
    RevisionExecution,
    SourceSetMap,
    StructuralEvidence,
    TargetValidation,
    TaskOutcome,
    UpdateEvidence,
    ValidationEvidence,
)
from .serialization import from_db_case, load_snapshot, save_snapshot, to_db

__all__ = [
    "CaseBundle",
    "CaseMeta",
    "UpdateEvidence",
    "ExecutionEvidence",
    "RevisionExecution",
    "TaskOutcome",
    "ErrorObservation",
    "StructuralEvidence",
    "SourceSetMap",
    "RepairEvidence",
    "LocalizationResult",
    "PatchAttempt",
    "ValidationEvidence",
    "TargetValidation",
    "ExplanationEvidence",
    "from_db_case",
    "load_snapshot",
    "save_snapshot",
    "to_db",
]
