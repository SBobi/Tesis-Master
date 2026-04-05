"""Orchestrate the full update ingestion pipeline for a single Dependabot PR.

Flow:
  1. Fetch PR metadata + file list + before/after TOML contents  (pr_fetcher)
  2. Detect version changes from catalog diff                     (version_catalog)
  3. Classify each change → UpdateClass                          (event_classifier)
  4. Persist Repository, DependencyEvent, DependencyDiffs to DB  (repositories)
  5. Build and return UpdateEvidence + DependencyUpdateEvent      (domain models)

The caller is responsible for session lifetime (commit / rollback).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.evidence import UpdateEvidence
from ..domain.events import DependencyUpdateEvent, UpdateClass, VersionChange
from ..storage.repositories import (
    DependencyDiffRepo,
    DependencyEventRepo,
    RepositoryRepo,
    RepairCaseRepo,
)
from ..utils.log import get_logger
from .event_classifier import classify_all, dominant_class
from .pr_fetcher import PRFetchResult, fetch_pr, fetch_pr_from_url
from .version_catalog import detect_version_changes

log = get_logger(__name__)

# Primary catalog path we inspect for version changes.
_PRIMARY_CATALOG = "gradle/libs.versions.toml"
_FALLBACK_CATALOG = "libs.versions.toml"


@dataclass
class IngestResult:
    """Outcome of ingesting one PR."""

    pr: PRFetchResult
    update_evidence: UpdateEvidence
    domain_event: DependencyUpdateEvent
    case_id: str  # UUID of the new RepairCase row
    event_id: str  # UUID of the DependencyEvent row
    repository_id: str
    version_changes: list[VersionChange]
    update_class: UpdateClass
    skipped: bool = False
    skip_reason: str = ""


def ingest_pr_url(
    pr_url: str,
    session: Session,
    repo_local_path: str = "",
    artifact_dir: Optional[str] = None,
    detection_source: str = "dependabot",
) -> IngestResult:
    """Ingest a PR identified by its GitHub URL."""
    pr = fetch_pr_from_url(pr_url)
    return _ingest(pr, session, repo_local_path, artifact_dir, detection_source)


def ingest_pr(
    owner: str,
    repo: str,
    number: int,
    session: Session,
    repo_local_path: str = "",
    artifact_dir: Optional[str] = None,
    detection_source: str = "dependabot",
) -> IngestResult:
    """Ingest a PR identified by owner/repo/number."""
    pr = fetch_pr(owner, repo, number)
    return _ingest(pr, session, repo_local_path, artifact_dir, detection_source)


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _ingest(
    pr: PRFetchResult,
    session: Session,
    repo_local_path: str,
    artifact_dir: Optional[str],
    detection_source: str,
) -> IngestResult:
    repo_url = f"https://github.com/{pr.owner}/{pr.repo}"

    # --- Step 1: Detect version changes ------------------------------------
    catalog_path = _pick_catalog(pr)
    if catalog_path is None:
        return _skipped(pr, repo_url, "No version catalog found in PR diff")

    before_text = pr.before_contents.get(catalog_path, "")
    after_text = pr.after_contents.get(catalog_path, "")

    if not before_text and not after_text:
        return _skipped(pr, repo_url, f"Could not fetch catalog content for {catalog_path}")

    if before_text == after_text:
        return _skipped(pr, repo_url, "Catalog content identical before/after — no changes")

    change_set = detect_version_changes(
        before_text if before_text else "",
        after_text if after_text else "",
    )
    if not change_set.has_changes:
        return _skipped(pr, repo_url, "No version changes detected in catalog diff")

    version_changes = change_set.changes
    log.info(
        "PR %s/%s#%d: %d version change(s) detected",
        pr.owner, pr.repo, pr.number, len(version_changes),
    )

    # --- Step 2: Classify --------------------------------------------------
    build_file_paths = [f.filename for f in pr.files]
    classifications = classify_all(version_changes, build_file_paths)
    overall_class = dominant_class(list(classifications.values()))

    # --- Step 3: Build unified diff text -----------------------------------
    raw_diff = _build_raw_diff(pr, catalog_path)

    # --- Step 4: Persist to DB --------------------------------------------
    repo_row = RepositoryRepo(session).get_or_create(repo_url)

    event_row = DependencyEventRepo(session).create(
        repository_id=repo_row.id,
        update_class=overall_class.value,
        pr_ref=pr.pr_ref,
        raw_diff=raw_diff,
        source=detection_source,
    )

    diff_repo = DependencyDiffRepo(session)
    for vc in version_changes:
        diff_repo.create(
            dependency_event_id=event_row.id,
            dependency_group=vc.dependency_group,
            version_before=vc.before,
            version_after=vc.after,
            version_key=vc.version_key,
        )

    case_row = RepairCaseRepo(session).create(
        dependency_event_id=event_row.id,
        artifact_dir=artifact_dir,
    )

    log.info(
        "Ingested PR %s/%s#%d → case_id=%s update_class=%s",
        pr.owner, pr.repo, pr.number, case_row.id, overall_class.value,
    )

    # --- Step 5: Build domain objects and UpdateEvidence ------------------
    domain_event = DependencyUpdateEvent(
        repo_url=repo_url,
        repo_local_path=repo_local_path,
        pr_ref=pr.pr_ref,
        version_changes=version_changes,
        update_class=overall_class,
        raw_diff=raw_diff,
        build_file_paths=build_file_paths,
    )

    update_evidence = UpdateEvidence(
        update_event=domain_event,
        version_changes=version_changes,
        update_class=overall_class,
        build_file_diff=raw_diff,
        detection_source=detection_source,
    )

    return IngestResult(
        pr=pr,
        update_evidence=update_evidence,
        domain_event=domain_event,
        case_id=case_row.id,
        event_id=event_row.id,
        repository_id=repo_row.id,
        version_changes=version_changes,
        update_class=overall_class,
    )


def _pick_catalog(pr: PRFetchResult) -> Optional[str]:
    """Return the catalog filename we should analyse (prefer primary)."""
    for path in (_PRIMARY_CATALOG, _FALLBACK_CATALOG):
        if path in pr.after_contents or path in pr.before_contents:
            return path
    # Fall back to any catalog-like file that changed
    for f in pr.files:
        if f.filename.endswith("libs.versions.toml"):
            return f.filename
    return None


def _build_raw_diff(pr: PRFetchResult, catalog_path: str) -> str:
    """Return the patch text for the catalog file, or an empty string."""
    for f in pr.files:
        if f.filename == catalog_path:
            return f.patch
    return ""


def _skipped(pr: PRFetchResult, repo_url: str, reason: str) -> IngestResult:
    log.warning("Skipping PR %s/%s#%d: %s", pr.owner, pr.repo, pr.number, reason)
    empty_event = DependencyUpdateEvent(repo_url=repo_url, pr_ref=pr.pr_ref)
    empty_evidence = UpdateEvidence(
        update_event=empty_event,
        version_changes=[],
        update_class=UpdateClass.UNKNOWN,
    )
    return IngestResult(
        pr=pr,
        update_evidence=empty_evidence,
        domain_event=empty_event,
        case_id="",
        event_id="",
        repository_id="",
        version_changes=[],
        update_class=UpdateClass.UNKNOWN,
        skipped=True,
        skip_reason=reason,
    )
