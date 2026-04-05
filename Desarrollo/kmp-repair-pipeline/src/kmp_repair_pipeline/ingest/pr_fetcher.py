"""Fetch Dependabot PR metadata and before/after TOML content from GitHub.

Uses the GitHub REST API via `github_client`. The fetcher returns a
`PRFetchResult` which is consumed by `event_builder.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import github_client as gh
from ..utils.log import get_logger

log = get_logger(__name__)

# Paths we recognise as version catalog / build files inside a KMP repo.
_CATALOG_PATHS = [
    "gradle/libs.versions.toml",
    "libs.versions.toml",
    "build.gradle.kts",
    "build.gradle",
    "settings.gradle.kts",
    "settings.gradle",
    "gradle/wrapper/gradle-wrapper.properties",
]


@dataclass
class PRFile:
    filename: str
    status: str  # "added" | "modified" | "removed" | "renamed"
    additions: int
    deletions: int
    patch: str = ""  # unified diff patch; empty when file is binary or large


@dataclass
class PRFetchResult:
    owner: str
    repo: str
    number: int
    title: str
    body: str
    state: str  # "open" | "closed" | "merged"
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    files: list[PRFile] = field(default_factory=list)
    # Keyed by filename → raw text at that revision
    before_contents: dict[str, str] = field(default_factory=dict)
    after_contents: dict[str, str] = field(default_factory=dict)

    @property
    def catalog_files_changed(self) -> list[str]:
        return [f.filename for f in self.files if f.filename in _CATALOG_PATHS]

    @property
    def pr_ref(self) -> str:
        return f"pull/{self.number}"


def fetch_pr(owner: str, repo: str, number: int) -> PRFetchResult:
    """Fetch PR metadata, file list, and before/after content for catalog files."""
    log.info("Fetching PR %s/%s#%d", owner, repo, number)

    pr_data = gh.get(f"/repos/{owner}/{repo}/pulls/{number}")
    files = _fetch_pr_files(owner, repo, number)

    result = PRFetchResult(
        owner=owner,
        repo=repo,
        number=number,
        title=pr_data.get("title", ""),
        body=pr_data.get("body") or "",
        state=pr_data.get("state", "open"),
        head_sha=pr_data["head"]["sha"],
        base_sha=pr_data["base"]["sha"],
        head_ref=pr_data["head"]["ref"],
        base_ref=pr_data["base"]["ref"],
        files=files,
    )

    _populate_file_contents(owner, repo, result)
    return result


def fetch_pr_from_url(pr_url: str) -> PRFetchResult:
    """Convenience wrapper accepting a GitHub PR URL."""
    owner, repo, number = gh.parse_pr_url(pr_url)
    return fetch_pr(owner, repo, number)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_pr_files(owner: str, repo: str, number: int) -> list[PRFile]:
    """Fetch list of changed files (max 300 via pagination)."""
    files: list[PRFile] = []
    page = 1
    while True:
        data = gh.get(
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            params={"per_page": 100, "page": page},
        )
        if not data:
            break
        for f in data:
            files.append(
                PRFile(
                    filename=f["filename"],
                    status=f["status"],
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch", ""),
                )
            )
        if len(data) < 100:
            break
        page += 1
        if page > 3:  # guard: max 300 files
            break
    return files


def _populate_file_contents(owner: str, repo: str, result: PRFetchResult) -> None:
    """Fetch raw before/after content for catalog files that were changed."""
    targets = set(result.catalog_files_changed)
    if not targets:
        log.debug("No catalog files changed in PR %s/%s#%d", owner, repo, result.number)
        return

    for filename in targets:
        # Before: base commit
        before = _fetch_file_at(owner, repo, filename, result.base_sha)
        if before is not None:
            result.before_contents[filename] = before

        # After: head commit
        after = _fetch_file_at(owner, repo, filename, result.head_sha)
        if after is not None:
            result.after_contents[filename] = after

        log.debug(
            "Fetched %s: before=%s after=%s",
            filename,
            "yes" if before else "missing",
            "yes" if after else "missing",
        )


def _fetch_file_at(owner: str, repo: str, path: str, ref: str) -> Optional[str]:
    """Fetch the raw text of a file at a specific git ref. Returns None if not found."""
    try:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        return gh.get_raw(raw_url)
    except gh.GitHubAPIError as exc:
        if exc.status == 404:
            return None
        raise
