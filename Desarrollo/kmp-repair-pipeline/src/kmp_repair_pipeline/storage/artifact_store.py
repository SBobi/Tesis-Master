"""Local artifact store — deterministic paths for all case artifacts.

Layout under data/artifacts/<case_id>/:
  shadow/             — ShadowManifest JSON and notes
  execution/
    before/           — per-task stdout, stderr, exit codes
    after/
    patched/
  patches/            — unified diffs per attempt (<attempt>_<mode>.diff)
  prompts/            — LLM prompt inputs per agent call
  responses/          — LLM responses per agent call
  explanations/       — JSON + Markdown explanation artifacts

All write methods return the (path, sha256) tuple for DB storage.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..utils.json_io import sha256_of_file
from ..utils.log import get_logger

log = get_logger(__name__)


class ArtifactStore:
    """Manages local artifact paths for one repair case."""

    def __init__(self, base_dir: str | Path, case_id: str) -> None:
        self.case_dir = Path(base_dir) / case_id
        self._init_dirs()

    def _init_dirs(self) -> None:
        for sub in (
            "shadow",
            "execution/before",
            "execution/after",
            "execution/patched",
            "patches",
            "prompts",
            "responses",
            "explanations",
        ):
            (self.case_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Shadow
    # ------------------------------------------------------------------

    def shadow_manifest_path(self) -> Path:
        return self.case_dir / "shadow" / "manifest.json"

    # ------------------------------------------------------------------
    # Execution artifacts
    # ------------------------------------------------------------------

    def task_stdout_path(self, revision_type: str, task_name: str) -> Path:
        safe = task_name.replace(":", "_").replace("/", "_")
        return self.case_dir / "execution" / revision_type / f"{safe}.stdout"

    def task_stderr_path(self, revision_type: str, task_name: str) -> Path:
        safe = task_name.replace(":", "_").replace("/", "_")
        return self.case_dir / "execution" / revision_type / f"{safe}.stderr"

    def write_task_output(
        self,
        revision_type: str,
        task_name: str,
        stdout: str,
        stderr: str,
    ) -> tuple[str, str, str, str]:
        """Write stdout and stderr to disk. Returns (stdout_path, stdout_sha256, stderr_path, stderr_sha256)."""
        out_path = self.task_stdout_path(revision_type, task_name)
        err_path = self.task_stderr_path(revision_type, task_name)
        out_path.write_text(stdout, encoding="utf-8")
        err_path.write_text(stderr, encoding="utf-8")
        return (
            str(out_path),
            sha256_of_file(out_path),
            str(err_path),
            sha256_of_file(err_path),
        )

    # ------------------------------------------------------------------
    # Patches
    # ------------------------------------------------------------------

    def patch_diff_path(self, attempt_number: int, repair_mode: str) -> Path:
        return self.case_dir / "patches" / f"{attempt_number:03d}_{repair_mode}.diff"

    def write_patch(self, attempt_number: int, repair_mode: str, diff_text: str) -> tuple[str, str]:
        path = self.patch_diff_path(attempt_number, repair_mode)
        path.write_text(diff_text, encoding="utf-8")
        return str(path), sha256_of_file(path)

    # ------------------------------------------------------------------
    # Prompts and responses
    # ------------------------------------------------------------------

    def prompt_path(self, agent_type: str, call_index: int) -> Path:
        return self.case_dir / "prompts" / f"{agent_type}_{call_index:04d}.txt"

    def response_path(self, agent_type: str, call_index: int) -> Path:
        return self.case_dir / "responses" / f"{agent_type}_{call_index:04d}.txt"

    def write_prompt(self, agent_type: str, call_index: int, content: str) -> tuple[str, str]:
        path = self.prompt_path(agent_type, call_index)
        path.write_text(content, encoding="utf-8")
        return str(path), sha256_of_file(path)

    def write_response(self, agent_type: str, call_index: int, content: str) -> tuple[str, str]:
        path = self.response_path(agent_type, call_index)
        path.write_text(content, encoding="utf-8")
        return str(path), sha256_of_file(path)

    # ------------------------------------------------------------------
    # Explanations
    # ------------------------------------------------------------------

    def explanation_json_path(self) -> Path:
        return self.case_dir / "explanations" / "explanation.json"

    def explanation_markdown_path(self) -> Path:
        return self.case_dir / "explanations" / "explanation.md"

    def write_explanation_json(self, content: str) -> tuple[str, str]:
        path = self.explanation_json_path()
        path.write_text(content, encoding="utf-8")
        return str(path), sha256_of_file(path)

    def write_explanation_markdown(self, content: str) -> tuple[str, str]:
        path = self.explanation_markdown_path()
        path.write_text(content, encoding="utf-8")
        return str(path), sha256_of_file(path)

    # ------------------------------------------------------------------
    # Integrity check
    # ------------------------------------------------------------------

    def verify_artifact(self, path: str, expected_sha256: str) -> bool:
        """Return True if file exists and matches the stored hash."""
        p = Path(path)
        if not p.exists():
            return False
        return sha256_of_file(p) == expected_sha256

    def list_artifacts(self) -> list[str]:
        """List all files under the case directory."""
        return [str(f.relative_to(self.case_dir)) for f in self.case_dir.rglob("*") if f.is_file()]

    def total_size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.case_dir.rglob("*") if f.is_file())
