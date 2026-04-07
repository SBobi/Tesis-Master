"""Thin GitHub API client using httpx + GITHUB_TOKEN.

Falls back to `gh auth token` if the env var is absent.
All methods raise `GitHubAPIError` on non-2xx responses.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import httpx

from ..utils.log import get_logger

log = get_logger(__name__)

_BASE = "https://api.github.com"


class GitHubAPIError(Exception):
    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"GitHub API {status} for {url}: {body[:200]}")
        self.status = status


def _get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            token = result.stdout.strip()
        except Exception:
            pass
    return token


def _headers() -> dict[str, str]:
    token = _get_token()
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get(path: str, params: dict | None = None) -> Any:
    """GET from the GitHub API. Path is relative (e.g. '/repos/owner/repo')."""
    url = _BASE + path
    with httpx.Client(timeout=30) as client:
        r = client.get(url, headers=_headers(), params=params or {})
    if r.status_code >= 400:
        raise GitHubAPIError(r.status_code, url, r.text)
    return r.json()


def get_raw(url: str) -> str:
    """GET raw content from any URL (e.g. raw.githubusercontent.com)."""
    with httpx.Client(timeout=30) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {_get_token()}"} if _get_token() else {})
    if r.status_code >= 400:
        raise GitHubAPIError(r.status_code, url, r.text)
    return r.text


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse 'https://github.com/owner/repo/pull/N' → (owner, repo, number)."""
    parts = url.rstrip("/").split("/")
    try:
        pull_idx = parts.index("pull")
        return parts[pull_idx - 2], parts[pull_idx - 1], int(parts[pull_idx + 1])
    except (ValueError, IndexError):
        raise ValueError(f"Cannot parse PR URL: {url}")
