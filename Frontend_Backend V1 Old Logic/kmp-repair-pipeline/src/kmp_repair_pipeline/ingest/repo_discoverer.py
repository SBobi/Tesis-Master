"""Discover KMP repositories with Dependabot PRs via GitHub search.

Uses the GitHub Search API (code search + repository search) to find
repositories that:
  - Contain `gradle/libs.versions.toml`             (KMP version catalog)
  - Have at least one open Dependabot PR
  - Meet minimum activity thresholds (stars, recent push)

Returns `DiscoveredRepo` instances; callers feed these into `event_builder`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import github_client as gh
from ..utils.log import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# KMP detection markers (any one is sufficient to classify a repo as KMP)
# ---------------------------------------------------------------------------
_KMP_CODE_MARKERS = [
    "kotlin-multiplatform",
    "multiplatform",         # plugin alias in settings.gradle.kts
    "sourceSets",            # KMP source-set config
    "commonMain",
    "androidMain",
    "iosMain",
    "cocoapods",
    "expect fun",
    "actual fun",
]

# Search query that combines KMP signals with Dependabot presence.
_BASE_QUERY = (
    "filename:libs.versions.toml "
    "path:gradle "
    "kotlin-multiplatform "
    "NOT fork:true"
)


@dataclass
class DiscoveredRepo:
    full_name: str          # "owner/repo"
    owner: str
    repo: str
    url: str
    stars: int
    default_branch: str
    open_dependabot_prs: list[int] = field(default_factory=list)  # PR numbers
    kmp_confirmed: bool = False
    has_version_catalog: bool = False
    commit_count: int = 0
    non_bot_contributors: int = 0
    last_non_bot_commit_at: str = ""
    excluded_reason: str = ""

    @property
    def pr_urls(self) -> list[str]:
        return [
            f"https://github.com/{self.full_name}/pull/{n}"
            for n in self.open_dependabot_prs
        ]


def discover(
    min_stars: int = 100,
    min_commits: int = 250,
    min_non_bot_contributors: int = 3,
    active_months: int = 18,
    require_kmp_targets: bool = True,
    max_repos: int = 50,
    max_prs_per_repo: int = 10,
) -> list[DiscoveredRepo]:
    """Search GitHub for KMP repos with open Dependabot PRs.

    Parameters
    ----------
    min_stars:
        Minimum star count to consider a repository.
    min_commits:
        Minimum commit count threshold (estimated from contributor totals).
    min_non_bot_contributors:
        Minimum number of non-bot contributors.
    active_months:
        Require at least one non-bot commit within this window.
    require_kmp_targets:
        Require source trees containing commonMain, androidMain, and iosMain.
    max_repos:
        Upper bound on how many repositories to return.
    max_prs_per_repo:
        Maximum number of Dependabot PRs to fetch per repository.
    """
    candidates = _search_repos(max_repos * 3)  # over-fetch, then filter
    results: list[DiscoveredRepo] = []

    for candidate in candidates:
        if candidate.stars < min_stars:
            continue
        if len(results) >= max_repos:
            break

        # Verify version catalog presence and fetch open Dependabot PRs
        candidate.has_version_catalog = _has_version_catalog(candidate)
        if not candidate.has_version_catalog:
            continue

        eligible, reason = _meets_selection_criteria(
            candidate,
            min_stars=min_stars,
            min_commits=min_commits,
            min_non_bot_contributors=min_non_bot_contributors,
            active_months=active_months,
            require_kmp_targets=require_kmp_targets,
        )
        if not eligible:
            candidate.excluded_reason = reason
            log.debug("Excluded %s: %s", candidate.full_name, reason)
            continue

        prs = _open_dependabot_prs(candidate, max_prs_per_repo)
        if not prs:
            continue

        candidate.open_dependabot_prs = prs
        candidate.kmp_confirmed = True  # if it passed the query it has KMP markers
        results.append(candidate)
        log.info("Discovered %s — %d Dependabot PR(s)", candidate.full_name, len(prs))

    log.info("Discovery complete: %d repositories found", len(results))
    return results


def discover_prs_for_repo(owner: str, repo: str, max_prs: int = 20) -> list[int]:
    """List open Dependabot PR numbers for a specific repository."""
    full = f"{owner}/{repo}"
    stub = DiscoveredRepo(full, owner, repo, f"https://github.com/{full}", 0, "main")
    return _open_dependabot_prs(stub, max_prs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _search_repos(limit: int) -> list[DiscoveredRepo]:
    """Use GitHub code search to find KMP repos with version catalogs."""
    repos: list[DiscoveredRepo] = []
    seen: set[str] = set()
    page = 1

    while len(repos) < limit:
        try:
            data = gh.get(
                "/search/code",
                params={
                    "q": _BASE_QUERY,
                    "per_page": 30,
                    "page": page,
                },
            )
        except gh.GitHubAPIError as exc:
            log.warning("Search API error (page %d): %s", page, exc)
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            repo_data = item.get("repository", {})
            full_name = repo_data.get("full_name", "")
            if not full_name or full_name in seen:
                continue
            seen.add(full_name)
            owner, _, repo_name = full_name.partition("/")
            repos.append(
                DiscoveredRepo(
                    full_name=full_name,
                    owner=owner,
                    repo=repo_name,
                    url=f"https://github.com/{full_name}",
                    stars=repo_data.get("stargazers_count", 0),
                    default_branch=repo_data.get("default_branch", "main"),
                )
            )

        if len(items) < 30:
            break
        page += 1
        if page > 10:  # respect rate limits — max 300 code search results
            break

    return repos


def _has_version_catalog(repo: DiscoveredRepo) -> bool:
    """Check whether `gradle/libs.versions.toml` exists in the repo."""
    try:
        gh.get(f"/repos/{repo.owner}/{repo.repo}/contents/gradle/libs.versions.toml")
        return True
    except gh.GitHubAPIError as exc:
        if exc.status == 404:
            return False
        log.warning("Unexpected error checking catalog for %s: %s", repo.full_name, exc)
        return False


def _open_dependabot_prs(repo: DiscoveredRepo, max_prs: int) -> list[int]:
    """Return PR numbers for open Dependabot pull requests."""
    pr_numbers: list[int] = []
    page = 1
    while len(pr_numbers) < max_prs:
        try:
            prs = gh.get(
                f"/repos/{repo.owner}/{repo.repo}/pulls",
                params={
                    "state": "open",
                    "per_page": 30,
                    "page": page,
                },
            )
        except gh.GitHubAPIError as exc:
            log.warning("PR list error for %s: %s", repo.full_name, exc)
            break

        if not prs:
            break

        for pr in prs:
            user = pr.get("user", {})
            if user.get("login", "").lower() == "dependabot[bot]" or user.get("type") == "Bot":
                label_names = [lb["name"] for lb in pr.get("labels", [])]
                if _is_dependabot_pr(pr, label_names):
                    pr_numbers.append(pr["number"])
                    if len(pr_numbers) >= max_prs:
                        break

        if len(prs) < 30:
            break
        page += 1

    return pr_numbers


def _is_dependabot_pr(pr: dict, labels: list[str]) -> bool:
    """Heuristic: is this a Dependabot dependency update PR?"""
    login = pr.get("user", {}).get("login", "").lower()
    if "dependabot" in login:
        return True
    title = pr.get("title", "").lower()
    if "bump" in title or "update" in title:
        if any(lb in labels for lb in ("dependencies", "dependabot")):
            return True
    return False


def _meets_selection_criteria(
    repo: DiscoveredRepo,
    min_stars: int,
    min_commits: int,
    min_non_bot_contributors: int,
    active_months: int,
    require_kmp_targets: bool,
) -> tuple[bool, str]:
    """Check thesis repository selection criteria for one discovered repo."""
    try:
        metadata = gh.get(f"/repos/{repo.owner}/{repo.repo}")
    except gh.GitHubAPIError as exc:
        return False, f"metadata lookup failed ({exc.status})"

    stars = int(metadata.get("stargazers_count") or repo.stars or 0)
    repo.stars = stars
    if stars < min_stars:
        return False, f"stars={stars} < {min_stars}"

    if bool(metadata.get("archived", False)):
        return False, "repository archived"

    contributors = _list_contributors(repo.owner, repo.repo)
    non_bot_contributors = [c for c in contributors if not _is_bot_account(c)]
    repo.non_bot_contributors = len(non_bot_contributors)
    if repo.non_bot_contributors < min_non_bot_contributors:
        return False, f"non-bot contributors={repo.non_bot_contributors} < {min_non_bot_contributors}"

    commit_count = sum(int(c.get("contributions") or 0) for c in non_bot_contributors)
    repo.commit_count = commit_count
    if commit_count < min_commits:
        return False, f"estimated commits={commit_count} < {min_commits}"

    latest_non_bot = _latest_non_bot_commit_at(repo.owner, repo.repo)
    if latest_non_bot is None:
        return False, "no non-bot commit found"

    repo.last_non_bot_commit_at = latest_non_bot.isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * active_months)
    if latest_non_bot < cutoff:
        return False, "no recent non-bot commit in activity window"

    if require_kmp_targets and not _has_required_kmp_targets(repo):
        return False, "missing one or more required KMP targets (commonMain/androidMain/iosMain)"

    return True, ""


def _list_contributors(owner: str, repo: str, max_pages: int = 5) -> list[dict]:
    contributors: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            data = gh.get(
                f"/repos/{owner}/{repo}/contributors",
                params={"per_page": 100, "page": page, "anon": "true"},
            )
        except gh.GitHubAPIError:
            break
        if not data:
            break
        contributors.extend(data)
        if len(data) < 100:
            break
    return contributors


def _is_bot_account(contributor: dict) -> bool:
    login = str(contributor.get("login") or "").lower()
    account_type = str(contributor.get("type") or "").lower()
    return account_type == "bot" or login.endswith("[bot]") or login == "dependabot"


def _latest_non_bot_commit_at(owner: str, repo: str) -> datetime | None:
    """Return latest non-bot commit datetime, or None if none found."""
    try:
        commits = gh.get(
            f"/repos/{owner}/{repo}/commits",
            params={"per_page": 50, "page": 1},
        )
    except gh.GitHubAPIError:
        return None

    for commit in commits:
        author = commit.get("author")
        if author is not None and _is_bot_account(author):
            continue
        date_raw = (commit.get("commit") or {}).get("author", {}).get("date")
        parsed = _parse_github_datetime(date_raw)
        if parsed is not None:
            return parsed
    return None


def _parse_github_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _has_required_kmp_targets(repo: DiscoveredRepo) -> bool:
    """Check that repository tree includes commonMain + androidMain + iosMain."""
    try:
        tree_data = gh.get(
            f"/repos/{repo.owner}/{repo.repo}/git/trees/{repo.default_branch}",
            params={"recursive": "1"},
        )
    except gh.GitHubAPIError:
        return False

    tree = tree_data.get("tree", [])
    paths = [node.get("path", "") for node in tree if node.get("type") == "blob"]

    has_common = any("/commonMain/" in p or p.startswith("src/commonMain/") for p in paths)
    has_android = any("/androidMain/" in p or p.startswith("src/androidMain/") for p in paths)
    has_ios = any("/iosMain/" in p or p.startswith("src/iosMain/") for p in paths)
    return has_common and has_android and has_ios
