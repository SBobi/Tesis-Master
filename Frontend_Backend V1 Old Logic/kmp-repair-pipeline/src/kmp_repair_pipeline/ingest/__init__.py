"""Update ingestion — Stage 1 of the thesis pipeline."""

from .event_builder import IngestResult, ingest_pr, ingest_pr_url
from .event_classifier import classify_all, classify_update, dominant_class
from .pr_fetcher import PRFetchResult, fetch_pr, fetch_pr_from_url
from .repo_discoverer import DiscoveredRepo, discover, discover_prs_for_repo

__all__ = [
    "IngestResult",
    "ingest_pr",
    "ingest_pr_url",
    "classify_update",
    "classify_all",
    "dominant_class",
    "PRFetchResult",
    "fetch_pr",
    "fetch_pr_from_url",
    "DiscoveredRepo",
    "discover",
    "discover_prs_for_repo",
]
