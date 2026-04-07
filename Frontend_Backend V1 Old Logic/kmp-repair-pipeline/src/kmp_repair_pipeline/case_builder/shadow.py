"""Before/after shadow build — copies project and pins dependency versions.

Ported from prototype phase1_shadow/shadow.py with:
- package rename
- config type replaced with explicit parameters
- reproducibility manifest added
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel

from ..domain.events import VersionChange
from ..ingest.toml_parser import VersionCatalog
from ..utils.log import get_logger

log = get_logger(__name__)


class ShadowManifest(BaseModel):
    """Reproducibility manifest for a before/after shadow pair."""
    before_dir: str
    after_dir: str
    version_change: VersionChange
    init_script_injected: bool = False


def _find_version_toml(project_dir: Path) -> Path | None:
    candidates = [
        project_dir / "gradle" / "libs.versions.toml",
        project_dir / "libs.versions.toml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _copy_project(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".idea"),
    )


def _inject_init_script(project_dir: Path, init_script_src: Path) -> bool:
    gradle_dir = project_dir / "gradle"
    gradle_dir.mkdir(exist_ok=True)
    dest = gradle_dir / "impact-analyzer-init.gradle.kts"
    if init_script_src.exists():
        shutil.copy2(init_script_src, dest)
        log.info(f"Injected init script → {dest}")
        return True
    log.warning(f"Init script not found at {init_script_src}")
    return False


def build_shadow(
    repo_path: str | Path,
    dependency_group: str,
    before_version: str,
    after_version: str,
    output_dir: str | Path,
    init_script_path: str | Path | None = None,
) -> ShadowManifest:
    """Create BEFORE and AFTER project copies with pinned versions.

    Returns a ShadowManifest that records exactly what was created.
    """
    output = Path(output_dir) / "shadow"
    output.mkdir(parents=True, exist_ok=True)

    repo = Path(repo_path)
    if not repo.is_dir():
        raise FileNotFoundError(f"Repository not found: {repo}")

    before_dir = output / "before"
    after_dir = output / "after"

    log.info("Copying project to BEFORE and AFTER shadow directories...")
    _copy_project(repo, before_dir)
    _copy_project(repo, after_dir)

    # Inject Gradle init script if provided
    injected = False
    if init_script_path:
        init_path = Path(init_script_path)
        injected_before = _inject_init_script(before_dir, init_path)
        injected_after = _inject_init_script(after_dir, init_path)
        injected = injected_before and injected_after

    # Locate and update the version catalog
    before_toml_path = _find_version_toml(before_dir)
    if before_toml_path is None:
        raise FileNotFoundError("libs.versions.toml not found in project")

    before_catalog = VersionCatalog(before_toml_path)
    version_key = before_catalog.find_version_key(dependency_group)
    if version_key is None:
        raise ValueError(
            f"Dependency group '{dependency_group}' not found in version catalog"
        )

    current = before_catalog.get_version(version_key)
    log.info(f"Found {dependency_group} → version key '{version_key}' = {current}")

    before_catalog.set_version(version_key, before_version)

    after_toml_path = _find_version_toml(after_dir)
    after_catalog = VersionCatalog(after_toml_path)
    after_catalog.set_version(version_key, after_version)

    manifest = ShadowManifest(
        before_dir=str(before_dir),
        after_dir=str(after_dir),
        version_change=VersionChange(
            dependency_group=dependency_group,
            version_key=version_key,
            before=before_version,
            after=after_version,
        ),
        init_script_injected=injected,
    )

    log.info("[bold green]Shadow build complete[/bold green]")
    return manifest
