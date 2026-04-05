"""Pipeline configuration — extended from prototype AnalysisConfig."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PipelineConfig:
    # --- Repository ---
    repo_path: str = ""
    repo_url: str = ""

    # --- Dependency target ---
    dependency_group: str = ""
    before_version: str = ""
    after_version: str = ""

    # --- Paths ---
    output_dir: str = "output"
    artifact_dir: str = "data/artifacts"
    init_script_path: str = ""

    # --- Analysis flags ---
    skip_dynamic: bool = False
    extra_seed_packages: list[str] = field(default_factory=list)

    # --- Dynamic analysis (deferred) ---
    droidbot_timeout: int = 120
    droidbot_policy: str = "dfs_greedy"
    before_apk: str = ""
    after_apk: str = ""
    droidbot_before_output: str = ""
    droidbot_after_output: str = ""

    # --- Database (Phase 2) ---
    db_url: str = ""

    # --- LLM provider (Phase 9) ---
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = ""

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PipelineConfig":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Read sensitive values from environment variables."""
        cfg = cls()
        cfg.db_url = os.environ.get("KMP_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
        cfg.llm_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return cfg

    def resolve_init_script(self) -> Path:
        if self.init_script_path:
            return Path(self.init_script_path)
        return Path(__file__).parent.parent.parent.parent / "gradle-init" / "impact-analyzer-init.gradle.kts"

    def resolve_artifact_dir(self, case_id: str = "") -> Path:
        base = Path(self.artifact_dir)
        if case_id:
            return base / case_id
        return base
