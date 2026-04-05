"""Patch synthesis — Stage 4 of the thesis pipeline."""

from .patch_applier import PatchApplicationResult, apply_patch, extract_touched_files
from .repair_agent import AGENT_TYPE, AgentRepairOutput, run_repair_agent
from .repairer import REPAIR_MODES, RepairRunResult, repair

__all__ = [
    "repair",
    "RepairRunResult",
    "REPAIR_MODES",
    "run_repair_agent",
    "AgentRepairOutput",
    "apply_patch",
    "PatchApplicationResult",
    "extract_touched_files",
    "AGENT_TYPE",
]
