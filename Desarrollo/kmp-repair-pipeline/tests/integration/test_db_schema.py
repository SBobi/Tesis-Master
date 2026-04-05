"""Integration tests — DB schema, repositories, and artifact store."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.integration.conftest import requires_db

from kmp_repair_pipeline.storage.repositories import (
    AgentLogRepo,
    DependencyDiffRepo,
    DependencyEventRepo,
    ErrorObservationRepo,
    EvaluationMetricRepo,
    ExecutionRunRepo,
    LocalizationCandidateRepo,
    PatchAttemptRepo,
    RepairCaseRepo,
    RepositoryRepo,
    RevisionRepo,
    SourceEntityRepo,
    TaskResultRepo,
    ValidationRunRepo,
)
from kmp_repair_pipeline.storage.artifact_store import ArtifactStore


@requires_db
class TestRepositoryRepo:
    def test_get_or_create(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/example/repo")
        assert repo.id is not None
        assert repo.url == "https://github.com/example/repo"

    def test_get_or_create_idempotent(self, db_session) -> None:
        r = RepositoryRepo(db_session)
        first = r.get_or_create("https://github.com/example/idempotent")
        second = r.get_or_create("https://github.com/example/idempotent")
        assert first.id == second.id

    def test_list_all(self, db_session) -> None:
        r = RepositoryRepo(db_session)
        r.get_or_create("https://github.com/example/list-test")
        repos = r.list_all()
        assert len(repos) >= 1


@requires_db
class TestFullCaseHierarchy:
    """Build a complete repair case record and verify the hierarchy."""

    def test_create_full_hierarchy(self, db_session) -> None:
        # 1. Repository
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/kmp-app")

        # 2. Dependency event
        event_repo = DependencyEventRepo(db_session)
        event = event_repo.create(
            repository_id=repo.id,
            update_class="direct_library",
            pr_ref="pull/42",
            source="toml_diff",
        )
        assert event.id is not None

        # 3. Dependency diff
        diff = DependencyDiffRepo(db_session).create(
            dependency_event_id=event.id,
            dependency_group="io.ktor",
            version_before="2.3.0",
            version_after="2.3.5",
            version_key="ktor",
        )
        assert diff.dependency_group == "io.ktor"

        # 4. Repair case
        case = RepairCaseRepo(db_session).create(dependency_event_id=event.id)
        assert case.status == "CREATED"

        # 5. Revision
        rev = RevisionRepo(db_session).create(
            repair_case_id=case.id,
            revision_type="before",
            git_sha="abc123",
            local_path="/tmp/before",
        )
        assert rev.revision_type == "before"

        # Duplicate revision type raises integrity error
        import sqlalchemy.exc
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            RevisionRepo(db_session).create(
                repair_case_id=case.id,
                revision_type="before",
            )

    def test_execution_run_and_tasks(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/exec-run")
        event = DependencyEventRepo(db_session).create(repo.id, "direct_library")
        case = RepairCaseRepo(db_session).create(event.id)

        run = ExecutionRunRepo(db_session).create(
            repair_case_id=case.id,
            revision_type="after",
            profile="linux-fast",
            env_metadata={"java_version": "17", "gradle": "8.5"},
        )
        assert run.env_metadata["java_version"] == "17"

        task = TaskResultRepo(db_session).create(
            execution_run_id=run.id,
            task_name=":compileCommonMainKotlinMetadata",
            exit_code=1,
            status="FAILED_BUILD",
        )
        assert task.exit_code == 1

        error = ErrorObservationRepo(db_session).create(
            task_result_id=task.id,
            error_type="COMPILE_ERROR",
            file_path="src/commonMain/kotlin/App.kt",
            line=42,
            message="Unresolved reference: HttpClient",
        )
        assert error.file_path == "src/commonMain/kotlin/App.kt"
        assert error.line == 42

    def test_source_entities_and_patch(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/source-patch")
        event = DependencyEventRepo(db_session).create(repo.id, "direct_library")
        case = RepairCaseRepo(db_session).create(event.id)

        entity = SourceEntityRepo(db_session).create(
            repair_case_id=case.id,
            file_path="src/commonMain/kotlin/App.kt",
            source_set="common",
            fqcn="com.example.App",
            is_expect=True,
        )
        assert entity.is_expect is True

        candidate = LocalizationCandidateRepo(db_session).create(
            repair_case_id=case.id,
            rank=1,
            score=0.92,
            classification="shared_code",
            file_path="src/commonMain/kotlin/App.kt",
            source_set="common",
            source_entity_id=entity.id,
            score_breakdown={"static": 0.8, "dynamic": 0.12},
        )
        assert candidate.score_breakdown["static"] == 0.8

        patch = PatchAttemptRepo(db_session).create(
            repair_case_id=case.id,
            attempt_number=1,
            repair_mode="full_thesis",
            model_id="claude-sonnet-4-6",
        )
        assert patch.repair_mode == "full_thesis"

    def test_validation_run_unavailable(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/validation")
        event = DependencyEventRepo(db_session).create(repo.id, "direct_library")
        case = RepairCaseRepo(db_session).create(event.id)
        patch = PatchAttemptRepo(db_session).create(case.id, 1, "full_thesis")

        vrun = ValidationRunRepo(db_session).create(
            repair_case_id=case.id,
            patch_attempt_id=patch.id,
            target="ios",
            status="NOT_RUN_ENVIRONMENT_UNAVAILABLE",
            unavailable_reason="Xcode not available on Linux",
        )
        assert vrun.status == "NOT_RUN_ENVIRONMENT_UNAVAILABLE"
        assert "Xcode" in vrun.unavailable_reason

    def test_agent_log(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/agent-log")
        event = DependencyEventRepo(db_session).create(repo.id, "direct_library")
        case = RepairCaseRepo(db_session).create(event.id)

        entry = AgentLogRepo(db_session).create(
            repair_case_id=case.id,
            agent_type="RepairAgent",
            call_index=0,
            model_id="claude-sonnet-4-6",
            tokens_in=1500,
            tokens_out=300,
        )
        assert entry.agent_type == "RepairAgent"
        assert entry.tokens_in == 1500

    def test_evaluation_metric_upsert(self, db_session) -> None:
        repo = RepositoryRepo(db_session).get_or_create("https://github.com/test/eval-metric")
        event = DependencyEventRepo(db_session).create(repo.id, "direct_library")
        case = RepairCaseRepo(db_session).create(event.id)

        em_repo = EvaluationMetricRepo(db_session)
        m1 = em_repo.upsert(case.id, "full_thesis", bsr=1.0, ctsr=1.0)
        assert m1.bsr == 1.0

        # Upsert again — should update
        m2 = em_repo.upsert(case.id, "full_thesis", bsr=0.5)
        assert m1.id == m2.id
        assert m2.bsr == 0.5

        # Different mode — new row
        m3 = em_repo.upsert(case.id, "raw_error", bsr=0.0)
        assert m3.id != m1.id


@requires_db
class TestArtifactStore:
    def test_init_creates_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp, "test-case-001")
            assert (Path(tmp) / "test-case-001" / "shadow").is_dir()
            assert (Path(tmp) / "test-case-001" / "patches").is_dir()
            assert (Path(tmp) / "test-case-001" / "prompts").is_dir()
            assert (Path(tmp) / "test-case-001" / "responses").is_dir()
            assert (Path(tmp) / "test-case-001" / "explanations").is_dir()

    def test_write_task_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp, "case-exec")
            out_path, out_sha, err_path, err_sha = store.write_task_output(
                "after", ":compileCommonMainKotlinMetadata", "BUILD FAILED", "error: Unresolved reference"
            )
            assert Path(out_path).read_text() == "BUILD FAILED"
            assert len(out_sha) == 64  # SHA-256 hex

    def test_write_task_output_validation_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp, "case-validation")
            out_path, out_sha, err_path, err_sha = store.write_task_output(
                "validation_002_full_thesis", ":compileCommonMainKotlinMetadata", "BUILD FAILED", "stderr"
            )
            assert Path(out_path).is_file()
            assert Path(err_path).is_file()
            assert len(out_sha) == 64
            assert len(err_sha) == 64

    def test_write_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp, "case-patch")
            diff = "--- a/App.kt\n+++ b/App.kt\n@@ -1 +1 @@\n-old\n+new\n"
            path, sha = store.write_patch(1, "full_thesis", diff)
            assert Path(path).read_text() == diff
            assert len(sha) == 64

    def test_write_prompt_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp, "case-agent")
            p_path, p_sha = store.write_prompt("RepairAgent", 0, "Fix this error: ...")
            r_path, r_sha = store.write_response("RepairAgent", 0, "Here is the patch: ...")
            assert "RepairAgent_0000.txt" in p_path
            assert "RepairAgent_0000.txt" in r_path

    def test_verify_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp, "case-verify")
            _, sha = store.write_patch(1, "raw_error", "patch content")
            path = str(store.patch_diff_path(1, "raw_error"))
            assert store.verify_artifact(path, sha) is True
            assert store.verify_artifact(path, "wrongsha") is False
            assert store.verify_artifact("/nonexistent/path", sha) is False
