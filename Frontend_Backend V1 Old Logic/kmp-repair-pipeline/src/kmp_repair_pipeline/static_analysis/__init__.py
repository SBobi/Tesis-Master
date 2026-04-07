"""KMP-aware static analysis — source parsing, symbol table, dependency graph."""

from .analyzer import run_static_analysis
from .structural_builder import StructuralAnalysisResult, analyze_case

__all__ = [
    "run_static_analysis",
    "analyze_case",
    "StructuralAnalysisResult",
]
