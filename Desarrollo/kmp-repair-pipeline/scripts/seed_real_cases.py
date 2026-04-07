"""Seed the database with real Dependabot PR cases from the demo repository.

Source: https://github.com/estebancastelblanco/kmp-production-sample-impact-demo
PRs 1-5 (open Dependabot updates as of 2026-04-04)

Run from the project root:
    python scripts/seed_real_cases.py                # seed + run Ktor (fresh)
    python scripts/seed_real_cases.py --seed-only    # only seed
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kmp_repair_pipeline.storage.models import RepairCase
from kmp_repair_pipeline.storage.db import get_session
from kmp_repair_pipeline.storage.repositories import (
    DependencyDiffRepo,
    DependencyEventRepo,
    RepairCaseRepo,
    RepositoryRepo,
)

REPO_URL = "https://github.com/estebancastelblanco/kmp-production-sample-impact-demo"
DEFAULT_PLAY_CASE_PR_REF = "pull/3" 

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed real demo cases and optionally run one case end-to-end."
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only seed DB records (do not run scripts/run_e2e.sh).",
    )
    parser.add_argument(
        "--no-fresh",
        action="store_true",
        help="Run e2e without --fresh.",
    )
    parser.add_argument(
        "--case-pr-ref",
        default=DEFAULT_PLAY_CASE_PR_REF,
        help=(
            "PR ref to run after seeding (default: pull/1, Ktor 3.1.3 -> 3.4.1)."
        ),
    )
    parser.add_argument(
        "--verbose-e2e",
        action="store_true",
        help="Pass --verbose to scripts/run_e2e.sh (show full technical logs).",
    )
    return parser.parse_args()


def _get_or_create_case_for_event(
    *,
    case_repo: RepairCaseRepo,
    event_id: str,
) -> tuple[RepairCase, bool]:
    """Return existing case for an event or create it if missing."""
    with case_repo._s.no_autoflush:
        stmt = (
            select(RepairCase)
            .where(RepairCase.dependency_event_id == event_id)
            .order_by(RepairCase.created_at.desc())
        )
        existing_case = case_repo._s.scalars(stmt).first()
        if existing_case:
            return existing_case, False

    created_case = case_repo.create(dependency_event_id=event_id)
    return created_case, True


def _run_case_e2e(case_id: str, *, fresh: bool, verbose: bool) -> None:
    project_root = Path(__file__).resolve().parent.parent
    run_script = project_root / "scripts" / "run_e2e.sh"

    cmd = ["bash", str(run_script), case_id]
    if fresh:
        cmd.append("--fresh")
    if verbose:
        cmd.append("--verbose")

    mode_bits = []
    if fresh:
        mode_bits.append("--fresh")
    if verbose:
        mode_bits.append("--verbose")
    mode = " ".join(mode_bits) if mode_bits else "normal"
    print(f"\n[PLAY] Running case {case_id[:8]} with run_e2e.sh ({mode})")
    subprocess.run(cmd, cwd=project_root, check=True)


def main() -> None:
    args = parse_args()
    run_after_seed = not args.seed_only
    fresh = not args.no_fresh
    target_pr_ref = args.case_pr_ref

    case_ids_by_pr_ref: dict[str, str] = {}

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

        existing_events_by_pr_ref = {
            e.pr_ref: e
            for e in event_repo.list_for_repo(repo.id)
            if e.pr_ref
        }

        for spec in CASES:
            event = existing_events_by_pr_ref.get(spec["pr_ref"])
            created_event = False

            if event is None:
                event = event_repo.create(
                    repository_id=repo.id,
                    update_class=spec["update_class"],
                    pr_ref=spec["pr_ref"],
                    pr_title=spec.get("title"),
                    source="dependabot_pr",
                )
                created_event = True
                existing_events_by_pr_ref[spec["pr_ref"]] = event

                for d in spec["diffs"]:
                    diff_repo.create(
                        dependency_event_id=event.id,
                        dependency_group=d["dependency_group"],
                        version_before=d["version_before"],
                        version_after=d["version_after"],
                        version_key=d["version_key"],
                    )

            case, created_case = _get_or_create_case_for_event(
                case_repo=case_repo,
                event_id=event.id,
            )
            case_ids_by_pr_ref[spec["pr_ref"]] = case.id

            if created_event or created_case:
                print(
                    f"  [OK] PR {spec['pr_ref']}  {spec['title']}\n"
                    f"       event={event.id[:8]}  case={case.id[:8]}"
                )
            else:
                print(
                    f"  [SKIP] PR {spec['pr_ref']}  {spec['title']} — already in DB\n"
                    f"         event={event.id[:8]}  case={case.id[:8]}"
                )

    print("\nSeed complete.")

    if not run_after_seed:
        return

    target_case_id = case_ids_by_pr_ref.get(target_pr_ref)
    if target_case_id is None:
        print(
            f"[PLAY] No case found for pr_ref={target_pr_ref!r}. "
            "Use --case-pr-ref with one of the seeded PR refs (pull/1..pull/5)."
        )
        return

    _run_case_e2e(target_case_id, fresh=fresh, verbose=args.verbose_e2e)


if __name__ == "__main__":
    main()
