"""Phase 11 — ExplanationAgent and reviewer-oriented explanation generation."""

from .explanation_agent import AGENT_TYPE, AgentExplanationOutput, render_markdown, run_explanation_agent
from .explainer import ExplainResult, explain

__all__ = [
    "explain",
    "ExplainResult",
    "run_explanation_agent",
    "AgentExplanationOutput",
    "render_markdown",
    "AGENT_TYPE",
]
