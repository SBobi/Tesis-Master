#!/usr/bin/env python3
"""Reset a repair case to INGESTED status.

Deletes all pipeline-run data for the given case (execution runs, patch
attempts, validation runs, explanations, metrics, localization candidates,
source entities, agent logs, job records, and status transitions) while
preserving the ingest record, dependency event, repository row, and any
cloned revision workspaces (so build-case does not need to re-clone).

Usage:
    python scripts/reset_case.py [case_id]
    python scripts/reset_case.py <case_id> --dry-run

Prerequisites:
    - .env must exist in the current directory or two levels up
    - PostgreSQL must be reachable
    - kmp_repair_pipeline must be installed (pip install -e ../../kmp-repair-pipeline)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root or scripts/ subdirectory.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent, _HERE.parent.parent):
    src = candidate / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))
        break

try:
    from dotenv import load_dotenv as _dotenv_load
    _dotenv_load(_HERE.parent / ".env", override=False)
    _dotenv_load(_HERE.parent.parent.parent / "kmp-repair-pipeline" / ".env", override=False)
except ImportError:
    pass

from kmp_repair_pipeline.storage.db import get_session_factory  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402


DEFAULT_CASE_ID = "3407b237-981f-40da-9623-4c4ac3c2087b"


# Deletion order respects all foreign-key constraints discovered in the
# production schema (see error_observations, validation_runs, expect_actual_links).
_DELETE_STEPS: list[tuple[str, str]] = [
    # 1. error_observations references task_results
    (
        "error_observations (via task_results)",
        """DELETE FROM error_observations
           WHERE task_result_id IN (
               SELECT tr.id FROM task_results tr
               JOIN execution_runs er ON tr.execution_run_id = er.id
               WHERE er.repair_case_id = :case_id
           )""",
    ),
    # 2. task_results references execution_runs
    (
        "task_results",
        """DELETE FROM task_results
           WHERE execution_run_id IN (
               SELECT id FROM execution_runs WHERE repair_case_id = :case_id
           )""",
    ),
    # 3. validation_runs references both execution_runs and patch_attempts
    (
        "validation_runs",
        "DELETE FROM validation_runs WHERE repair_case_id = :case_id",
    ),
    # 4. explanations references patch_attempts
    (
        "explanations",
        "DELETE FROM explanations WHERE repair_case_id = :case_id",
    ),
    # 5. execution_runs (now safe — task_results and validation_runs gone)
    (
        "execution_runs",
        "DELETE FROM execution_runs WHERE repair_case_id = :case_id",
    ),
    # 6. patch_attempts (now safe — explanations and validation_runs gone)
    (
        "patch_attempts",
        "DELETE FROM patch_attempts WHERE repair_case_id = :case_id",
    ),
    # 7. localization_candidates references source_entities
    (
        "localization_candidates",
        "DELETE FROM localization_candidates WHERE repair_case_id = :case_id",
    ),
    # 8. expect_actual_links references source_entities
    (
        "expect_actual_links (via source_entities)",
        """DELETE FROM expect_actual_links
           WHERE expect_entity_id IN (
               SELECT id FROM source_entities WHERE repair_case_id = :case_id
           )""",
    ),
    # 9. source_entities (now safe)
    (
        "source_entities",
        "DELETE FROM source_entities WHERE repair_case_id = :case_id",
    ),
    # 10. agent_logs, evaluation_metrics
    ("agent_logs", "DELETE FROM agent_logs WHERE repair_case_id = :case_id"),
    ("evaluation_metrics", "DELETE FROM evaluation_metrics WHERE repair_case_id = :case_id"),
    # 11. case_status_transitions references pipeline_jobs
    (
        "case_status_transitions",
        "DELETE FROM case_status_transitions WHERE repair_case_id = :case_id",
    ),
    # 12. pipeline_jobs (self-referencing parent_job_id — nullify first)
    (
        "pipeline_jobs (nullify parent_job_id)",
        "UPDATE pipeline_jobs SET parent_job_id = NULL WHERE repair_case_id = :case_id",
    ),
    ("pipeline_jobs", "DELETE FROM pipeline_jobs WHERE repair_case_id = :case_id"),
    # 13. Reset case status
    (
        "repair_cases (status -> INGESTED)",
        "UPDATE repair_cases SET status = 'INGESTED', updated_at = now() WHERE id = :case_id",
    ),
]


def _verify_case_exists(session, case_id: str) -> dict:
    row = session.execute(
        text(
            """SELECT rc.id, rc.status, de.pr_ref, de.pr_title, r.url
               FROM repair_cases rc
               JOIN dependency_events de ON rc.dependency_event_id = de.id
               JOIN repositories r ON de.repository_id = r.id
               WHERE rc.id = :case_id"""
        ),
        {"case_id": case_id},
    ).first()
    if row is None:
        print(f"ERROR: case {case_id!r} not found in database.", file=sys.stderr)
        sys.exit(1)
    return {"id": row[0], "status": row[1], "pr_ref": row[2], "pr_title": row[3], "url": row[4]}


def reset_case(case_id: str, *, dry_run: bool = False) -> None:
    factory = get_session_factory()
    session = factory()

    try:
        info = _verify_case_exists(session, case_id)
        print(f"Case : {info['id']}")
        print(f"Repo : {info['url']}")
        print(f"PR   : {info['pr_ref']} -- {info['pr_title']}")
        print(f"From : {info['status']} -> INGESTED")
        print()

        if dry_run:
            print("[dry-run] No changes committed.")
            return

        # Avoid hanging indefinitely when another session holds row locks.
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(text("SET LOCAL statement_timeout = '10min'"))

        total_deleted = 0
        for label, sql in _DELETE_STEPS:
            verb = "UPDATE" if sql.lstrip().upper().startswith("UPDATE") else "DELETE"
            print(f"  [{verb:6s}] {label} ...", flush=True)
            result = session.execute(text(sql), {"case_id": case_id})
            n = result.rowcount
            if n:
                print(f"           {label}: {n} row(s)")
            total_deleted += max(n, 0)

        session.commit()
        print()
        print(f"Reset complete. Total rows affected: {total_deleted}")
        print("Case is now INGESTED and ready for a fresh pipeline run.")

    except DBAPIError as exc:
        session.rollback()
        err_text = str(getattr(exc, "orig", exc)).lower()
        if "lock timeout" in err_text or "deadlock" in err_text:
            print(
                "ERROR: Reset blocked by another DB transaction (lock timeout).",
                file=sys.stderr,
            )
            print(
                "Stop stale processes and retry: pgrep -fl 'kmp-repair-worker|run_e2e.sh|reset_case.py'",
                file=sys.stderr,
            )
            sys.exit(2)
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset a repair case to INGESTED status (clears all pipeline-run data)."
    )
    parser.add_argument(
        "case_id",
        nargs="?",
        default=None,
        help=f"UUID of the repair case to reset (default: {DEFAULT_CASE_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without committing any changes",
    )
    args = parser.parse_args()
    case_id = args.case_id or DEFAULT_CASE_ID
    if args.case_id is None:
        print(f"No case_id provided; using default Ktor case: {DEFAULT_CASE_ID}")
    reset_case(case_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
