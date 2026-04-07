"""Validation status vocabulary and dynamic analysis types.

ValidationStatus uses explicit statuses as required by the thesis — never
collapse NOT_RUN_ENVIRONMENT_UNAVAILABLE into a generic failure.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ValidationStatus(str, Enum):
    """Explicit validation outcome status.

    Thesis non-negotiable: never claim iOS validation happened if the
    environment could not run it.
    """
    SUCCESS_REPOSITORY_LEVEL = "SUCCESS_REPOSITORY_LEVEL"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED_BUILD = "FAILED_BUILD"
    FAILED_TESTS = "FAILED_TESTS"
    NOT_RUN_ENVIRONMENT_UNAVAILABLE = "NOT_RUN_ENVIRONMENT_UNAVAILABLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN_YET = "NOT_RUN_YET"


# ---------------------------------------------------------------------------
# Dynamic analysis types (ported from prototype contracts.py)
# ---------------------------------------------------------------------------

class UTGNode(BaseModel):
    state_id: str = ""
    activity: str = ""
    state_str: str = ""
    screen_name: str = ""


class UTGEdge(BaseModel):
    source: str = ""
    target: str = ""
    action: str = ""


class UTGGraph(BaseModel):
    nodes: list[UTGNode] = Field(default_factory=list)
    edges: list[UTGEdge] = Field(default_factory=list)


class ScreenDiff(BaseModel):
    screen_name: str
    status: str  # "missing", "new", "changed"
    details: str = ""


class DynamicStatus(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class UIRegressions(BaseModel):
    status: DynamicStatus = DynamicStatus.SKIPPED
    blocked_reason: str = ""
    before_screens: list[str] = Field(default_factory=list)
    after_screens: list[str] = Field(default_factory=list)
    diffs: list[ScreenDiff] = Field(default_factory=list)
