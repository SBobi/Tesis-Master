"""Build a reproducible repair case from an ingested Dependabot PR.

Phase 5 orchestrator:
  1. Rehydrate CaseBundle from DB (Phase 3 machinery)
  2. Resolve the before/after git SHAs from the GitHub PR (or the DB if already stored)
  3. Clone before and after revisions locally into the artifact work directory
  4. Record revisions to DB (revisions table — git_sha, local_path)
  5. Set artifact_dir on repair_case
  6. Advance bundle status → SHADOW_BUILT
  7. Return the ready-to-execute CaseBundle

The caller is responsible for session commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..case_bundle.bundle import CaseBundle
from ..case_bundle.serialization import from_db_case, to_db
from ..storage.artifact_store import ArtifactStore
from ..storage.repositories import RepairCaseRepo, RevisionRepo
from ..utils.log import get_logger
from .repo_cloner import ClonerError, clone_before_after, is_git_repo

log = get_logger(__name__)

# Default artifact root (overridable via CLI / config)
_DEFAULT_ARTIFACT_BASE = Path("data/artifacts")


@dataclass
class CaseBuildResult:
    """Outcome of a Phase 5 case-build operation."""

    bundle: CaseBundle
    before_path: Path
    after_path: Path
    artifact_dir: Path
    already_built: bool = False  # True when a clone already existed and was reused


def build_case(
    case_id: str,
    session: Session,
    artifact_base: Path | str = _DEFAULT_ARTIFACT_BASE,
    work_base: Optional[Path | str] = None,
    overwrite_clone: bool = False,
) -> CaseBuildResult:
    """Build a reproducible repair case for `case_id`.

    Parameters
    ----------
    case_id:
        UUID of an existing repair_case row.
    session:
        Active SQLAlchemy session (caller controls commit).
    artifact_base:
        Root directory for all artifact stores (default: ``data/artifacts/``).
    work_base:
        Directory for git clones. Defaults to ``<artifact_base>/<case_id>/workspace/``.
    overwrite_clone:
        If True, delete and re-clone even if the directory already exists.
    """
    bundle = from_db_case(case_id, session)
    if bundle is None:
        raise ValueError(f"Case {case_id} not found in DB")

    if bundle.meta.status not in ("CREATED", "INGESTED", "SHADOW_BUILT"):
        log.warning(
            "Case %s already at status %s — re-building shadow anyway",
            case_id, bundle.meta.status,
        )

    # Resolve SHAs
    base_sha, head_sha = _resolve_shas(case_id, bundle, session)

    # Set up directories
    artifact_dir = Path(artifact_base).resolve() / case_id
    work_dir = (Path(work_base).resolve() if work_base else artifact_dir) / "workspace"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing revisions in the DB
    rev_repo = RevisionRepo(session)
    existing_before = rev_repo.get(case_id, "before")
    existing_after = rev_repo.get(case_id, "after")

    already_built = False

    if (
        existing_before
        and existing_before.local_path
        and is_git_repo(existing_before.local_path)
        and existing_after
        and existing_after.local_path
        and is_git_repo(existing_after.local_path)
        and not overwrite_clone
    ):
        before_path = Path(existing_before.local_path)
        after_path = Path(existing_after.local_path)
        already_built = True
        log.info("Reusing existing clones for case %s", case_id[:8])
    else:
        repo_url = bundle.meta.repository_url
        log.info(
            "Cloning %s before=%s after=%s",
            repo_url, base_sha[:12], head_sha[:12],
        )
        try:
            before_path, after_path = clone_before_after(
                repo_url=repo_url,
                base_sha=base_sha,
                head_sha=head_sha,
                work_dir=work_dir,
                overwrite=overwrite_clone,
            )
        except ClonerError as exc:
            raise RuntimeError(f"Failed to clone repository for case {case_id}: {exc}") from exc

        # Persist revision records (upsert via delete-then-insert approach)
        _upsert_revision(rev_repo, session, case_id, "before", base_sha, before_path)
        _upsert_revision(rev_repo, session, case_id, "after", head_sha, after_path)

    # Initialise artifact store (creates subdirectory layout)
    ArtifactStore(artifact_base, case_id)

    # Update repair_case: artifact_dir and status
    case_row = RepairCaseRepo(session).get_by_id(case_id)
    if case_row is not None:
        case_row.artifact_dir = str(artifact_dir)
        session.flush()

    # Advance bundle state
    bundle.meta.artifact_dir = str(artifact_dir)
    bundle.meta.status = "SHADOW_BUILT"
    # Also update the domain event's local path to the after-clone
    if bundle.update_evidence:
        bundle.update_evidence.update_event.repo_local_path = str(after_path)
    to_db(bundle, session)

    log.info(
        "Case %s built: before=%s after=%s artifact_dir=%s",
        case_id[:8], before_path, after_path, artifact_dir,
    )

    return CaseBuildResult(
        bundle=bundle,
        before_path=before_path,
        after_path=after_path,
        artifact_dir=artifact_dir,
        already_built=already_built,
    )


# ---------------------------------------------------------------------------
# SHA resolution
# ---------------------------------------------------------------------------


def _resolve_shas(case_id: str, bundle: CaseBundle, session: Session) -> tuple[str, str]:
    """Return (base_sha, head_sha) for the PR.

    Strategy (in order):
      1. Already stored in the revisions table → reuse
      2. Fetch from GitHub using the pr_ref stored in update_evidence
    """
    rev_repo = RevisionRepo(session)
    before_rev = rev_repo.get(case_id, "before")
    after_rev = rev_repo.get(case_id, "after")

    if before_rev and before_rev.git_sha and after_rev and after_rev.git_sha:
        return before_rev.git_sha, after_rev.git_sha

    # Fetch from GitHub
    pr_ref = bundle.update_evidence.update_event.pr_ref if bundle.update_evidence else None
    repo_url = bundle.meta.repository_url

    if not pr_ref:
        raise ValueError(f"Case {case_id}: no pr_ref in update_evidence — cannot resolve SHAs")

    from ..ingest.github_client import parse_pr_url, get

    # pr_ref is like "pull/5"
    pr_number = int(pr_ref.split("/")[-1])
    # repo_url is like "https://github.com/owner/repo"
    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]

    log.info("Fetching SHA from GitHub for %s/%s %s", owner, repo, pr_ref)
    pr_data = get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
    base_sha: str = pr_data["base"]["sha"]
    head_sha: str = pr_data["head"]["sha"]

    log.info("Resolved: base=%s head=%s", base_sha[:12], head_sha[:12])
    return base_sha, head_sha


def _upsert_revision(
    rev_repo: RevisionRepo,
    session: Session,
    case_id: str,
    revision_type: str,
    git_sha: str,
    local_path: Path,
) -> None:
    """Create or update a revision row."""
    from ..storage.models import Revision
    from sqlalchemy import select

    stmt = select(Revision).where(
        Revision.repair_case_id == case_id,
        Revision.revision_type == revision_type,
    )
    existing = session.scalars(stmt).first()
    if existing:
        existing.git_sha = git_sha
        existing.local_path = str(local_path)
        session.flush()
    else:
        rev_repo.create(
            repair_case_id=case_id,
            revision_type=revision_type,
            git_sha=git_sha,
            local_path=str(local_path),
        )
