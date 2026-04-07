"""Integration tests — Case Bundle DB rehydration."""

from __future__ import annotations

import pytest

from tests.integration.conftest import requires_db

from kmp_repair_pipeline.case_bundle.serialization import from_db_case, to_db
from kmp_repair_pipeline.storage.repositories import (
    DependencyDiffRepo,
    DependencyEventRepo,
    RepairCaseRepo,
    RepositoryRepo,
)


@requires_db
class TestBundleRehydration:
    def test_rehydrate_minimal_case(self, db_session) -> None:
        """Rehydrate a case that only has update evidence (no execution yet)."""
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/rehydrate")
        event = DependencyEventRepo(db_session).create(
            repository_id=repo.id,
            update_class="direct_library",
            pr_ref="pull/99",
            source="toml_diff",
        )
        DependencyDiffRepo(db_session).create(
            dependency_event_id=event.id,
            dependency_group="io.ktor",
            version_before="3.1.3",
            version_after="3.4.1",
            version_key="ktor",
        )
        case = RepairCaseRepo(db_session).create(event.id)
        db_session.commit()

        bundle = from_db_case(case.id, db_session)
        assert bundle is not None
        assert bundle.case_id == case.id
        assert bundle.meta.repository_url == "https://github.com/test/rehydrate"
        assert bundle.meta.status == "CREATED"

        # Update evidence should be populated
        assert bundle.update_evidence is not None
        assert len(bundle.update_evidence.version_changes) == 1
        vc = bundle.update_evidence.version_changes[0]
        assert vc.dependency_group == "io.ktor"
        assert vc.before == "3.1.3"
        assert vc.after == "3.4.1"

        # No execution evidence yet
        assert bundle.execution is None
        assert bundle.structural is None
        assert bundle.repair is None

    def test_rehydrate_nonexistent_returns_none(self, db_session) -> None:
        bundle = from_db_case("00000000-0000-0000-0000-000000000000", db_session)
        assert bundle is None

    def test_sync_status_to_db(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/sync-status")
        event = DependencyEventRepo(db_session).create(repo.id, "direct_library")
        DependencyDiffRepo(db_session).create(event.id, "io.ktor", "3.1.3", "3.4.1", "ktor")
        case = RepairCaseRepo(db_session).create(event.id)
        db_session.commit()

        bundle = from_db_case(case.id, db_session)
        assert bundle is not None

        # Simulate pipeline advancing the status
        bundle.meta.status = "EXECUTED"
        to_db(bundle, db_session)
        db_session.commit()

        # Reload from DB — status should be updated
        updated_case = RepairCaseRepo(db_session).get_by_id(case.id)
        assert updated_case.status == "EXECUTED"

    def test_rehydrate_real_seeded_cases(self, db_session) -> None:
        """Verify the 5 real Dependabot cases are rehydratable."""
        from kmp_repair_pipeline.storage.repositories import RepositoryRepo, RepairCaseRepo
        from sqlalchemy import select
        from kmp_repair_pipeline.storage.models import Repository

        stmt = select(Repository).where(
            Repository.url == "https://github.com/estebancastelblanco/kmp-production-sample-impact-demo"
        )
        repo = db_session.scalars(stmt).first()
        if repo is None:
            pytest.skip("Real seed data not present — run scripts/seed_real_cases.py first")

        cases = RepairCaseRepo(db_session).list_all()
        real_cases = [c for c in cases if c.dependency_event.repository_id == repo.id]
        assert len(real_cases) >= 5, f"Expected 5 real cases, found {len(real_cases)}"

        for case in real_cases[:2]:  # spot-check first 2
            bundle = from_db_case(case.id, db_session)
            assert bundle is not None
            assert bundle.update_evidence is not None
            assert len(bundle.update_evidence.version_changes) >= 1
            s = bundle.summary()
            assert len(s) > 10
