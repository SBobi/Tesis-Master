"""Seed the database with real Dependabot PR cases from the demo repository.

Source: https://github.com/estebancastelblanco/kmp-production-sample-impact-demo
PRs 1-5 (open Dependabot updates as of 2026-04-04)

Run from the project root:
    python scripts/seed_real_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kmp_repair_pipeline.storage.db import get_session
from kmp_repair_pipeline.storage.repositories import (
    DependencyDiffRepo,
    DependencyEventRepo,
    RepairCaseRepo,
    RepositoryRepo,
)

REPO_URL = "https://github.com/estebancastelblanco/kmp-production-sample-impact-demo"

# Real Dependabot PRs — extracted from the repository on 2026-04-04
CASES = [
    {
        "pr_ref": "pull/1",
        "pr_url": f"{REPO_URL}/pull/1",
        "title": "Bump ktor from 3.1.3 to 3.4.1",
        "update_class": "direct_library",
        "diffs": [
            {
                "dependency_group": "io.ktor",
                "version_key": "ktor",
                "version_before": "3.1.3",
                "version_after": "3.4.1",
            }
        ],
    },
    {
        "pr_ref": "pull/2",
        "pr_url": f"{REPO_URL}/pull/2",
        "title": "Bump agp from 8.10.1 to 9.1.0",
        "update_class": "plugin_toolchain",
        "diffs": [
            {
                "dependency_group": "com.android.application",
                "version_key": "agp",
                "version_before": "8.10.1",
                "version_after": "9.1.0",
            }
        ],
    },
    {
        "pr_ref": "pull/3",
        "pr_url": f"{REPO_URL}/pull/3",
        "title": "Bump koin from 4.1.0 to 4.2.0",
        "update_class": "direct_library",
        "diffs": [
            {
                "dependency_group": "io.insert-koin",
                "version_key": "koin",
                "version_before": "4.1.0",
                "version_after": "4.2.0",
            }
        ],
    },
    {
        "pr_ref": "pull/4",
        "pr_url": f"{REPO_URL}/pull/4",
        "title": "Bump kotlin from 2.2.0 to 2.3.20",
        "update_class": "plugin_toolchain",
        "diffs": [
            {
                "dependency_group": "org.jetbrains.kotlin.multiplatform",
                "version_key": "kotlin",
                "version_before": "2.2.0",
                "version_after": "2.3.20",
            },
            {
                "dependency_group": "org.jetbrains.kotlin.android",
                "version_key": "kotlin",
                "version_before": "2.2.0",
                "version_after": "2.3.20",
            },
            {
                "dependency_group": "org.jetbrains.kotlin.plugin.serialization",
                "version_key": "kotlin",
                "version_before": "2.2.0",
                "version_after": "2.3.20",
            },
        ],
    },
    {
        "pr_ref": "pull/5",
        "pr_url": f"{REPO_URL}/pull/5",
        "title": "Bump com.github.ben-manes.versions from 0.52.0 to 0.53.0",
        "update_class": "plugin_toolchain",
        "diffs": [
            {
                "dependency_group": "com.github.ben-manes.versions",
                "version_key": "dependencyUpdates",
                "version_before": "0.52.0",
                "version_after": "0.53.0",
            }
        ],
    },
]


def main() -> None:
    with get_session() as session:
        repo_repo = RepositoryRepo(session)
        event_repo = DependencyEventRepo(session)
        diff_repo = DependencyDiffRepo(session)
        case_repo = RepairCaseRepo(session)

        repo = repo_repo.get_or_create(REPO_URL)
        repo.owner = "estebancastelblanco"
        repo.name = "kmp-production-sample-impact-demo"
        repo.stars = 2
        session.flush()
        print(f"Repository: {repo.id}  {repo.url}")

        for spec in CASES:
            # Idempotent: skip if event for this PR already exists
            existing = [
                e for e in event_repo.list_for_repo(repo.id)
                if e.pr_ref == spec["pr_ref"]
            ]
            if existing:
                print(f"  [SKIP] {spec['title']} — already in DB")
                continue

            event = event_repo.create(
                repository_id=repo.id,
                update_class=spec["update_class"],
                pr_ref=spec["pr_ref"],
                source="dependabot_pr",
            )

            for d in spec["diffs"]:
                diff_repo.create(
                    dependency_event_id=event.id,
                    dependency_group=d["dependency_group"],
                    version_before=d["version_before"],
                    version_after=d["version_after"],
                    version_key=d["version_key"],
                )

            case = case_repo.create(dependency_event_id=event.id)

            print(
                f"  [OK] PR {spec['pr_ref']}  {spec['title']}\n"
                f"       event={event.id[:8]}  case={case.id[:8]}"
            )

    print("\nSeed complete.")


if __name__ == "__main__":
    main()
