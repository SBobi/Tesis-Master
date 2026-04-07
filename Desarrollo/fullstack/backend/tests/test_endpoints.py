"""Endpoint integration tests for kmp-repair-webapi.

All pipeline imports and Redis/DB connections are mocked so tests run
without any external infrastructure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kmp_repair_webapi.app import create_app, get_db

# ─── constants ────────────────────────────────────────────────────────────────

CASE_ID = "case-abc-123"
JOB_ID = "job-xyz-456"
_NOW = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _fake_job(status: str = "QUEUED") -> dict:
    return {
        "job_id": JOB_ID,
        "case_id": CASE_ID,
        "job_type": "RUN_STAGE",
        "stage": "localize",
        "start_from_stage": None,
        "status": status,
        "rq_job_id": "rq-1",
        "requested_by": "test",
        "command_preview": "kmp-repair localize case-abc-123",
        "params": {},
        "effective_params": {"localize": {}},
        "cancel_requested": False,
        "current_stage": None,
        "error_message": None,
        "result_summary": None,
        "log_path": None,
        "queued_at": _NOW.isoformat(),
        "started_at": None,
        "finished_at": None,
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


def _fake_case_detail() -> dict:
    return {
        "case": {
            "case_id": CASE_ID,
            "status": "INGESTED",
            "artifact_dir": None,
            "created_at": _NOW.isoformat(),
            "updated_at": _NOW.isoformat(),
            "repository": {"url": "https://github.com/owner/repo", "owner": "owner", "name": "repo"},
            "event": {"pr_ref": "PR#1", "pr_title": "Bump lib", "update_class": "minor", "raw_diff": ""},
        },
        "timeline": [],
        "evidence": {
            "update_evidence": {"changes": [], "raw_diff": ""},
            "execution_before_after": [],
            "structural_evidence": {"source_entities_count": 0, "sample": []},
            "localization_ranking": [],
            "patch_attempts": [],
            "validation_by_target": [],
            "explanations": [],
            "agent_logs": [],
            "metrics": [],
        },
        "jobs": [],
        "history": [],
    }


# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_session() -> MagicMock:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    session.scalars.return_value.first.return_value = None
    session.execute.return_value.all.return_value = []
    session.execute.return_value.first.return_value = None
    return session


@pytest.fixture()
def client(mock_session: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_session
    return TestClient(app)


# ─── GET /api/health ──────────────────────────────────────────────────────────


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "time" in body
    assert body["service"] == "kmp-repair-pipeline-web"


# ─── POST /api/cases ─────────────────────────────────────────────────────────


def test_create_case_success(client: TestClient) -> None:
    fake_detail = _fake_case_detail()

    with (
        patch("kmp_repair_webapi.app.ingest_pr_url") as mock_ingest,
        patch("kmp_repair_webapi.app.RepairCaseRepo") as MockCaseRepo,
        patch("kmp_repair_webapi.app.CaseStatusTransitionRepo"),
        patch("kmp_repair_webapi.app.get_case_detail", return_value=fake_detail),
    ):
        ingest_result = MagicMock()
        ingest_result.skipped = False
        ingest_result.case_id = CASE_ID
        ingest_result.update_class.value = "minor"
        ingest_result.version_changes = []
        mock_ingest.return_value = ingest_result

        case_row = MagicMock()
        case_row.status = "INGESTED"
        MockCaseRepo.return_value.get_by_id.return_value = case_row

        r = client.post(
            "/api/cases",
            json={"pr_url": "https://github.com/owner/repo/pull/1"},
        )

    assert r.status_code == 200
    assert r.json()["case"]["case_id"] == CASE_ID


def test_create_case_skipped_returns_422(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.ingest_pr_url") as mock_ingest:
        result = MagicMock()
        result.skipped = True
        result.skip_reason = "PR ya existe en la base de datos"
        mock_ingest.return_value = result

        r = client.post(
            "/api/cases",
            json={"pr_url": "https://github.com/owner/repo/pull/1"},
        )

    assert r.status_code == 422
    assert "ya existe" in r.json()["detail"]


def test_create_case_extra_field_rejected(client: TestClient) -> None:
    r = client.post(
        "/api/cases",
        json={"pr_url": "https://github.com/owner/repo/pull/1", "unknown_field": "x"},
    )
    assert r.status_code == 422


def test_create_case_missing_pr_url_rejected(client: TestClient) -> None:
    r = client.post("/api/cases", json={})
    assert r.status_code == 422


def test_create_case_short_url_rejected(client: TestClient) -> None:
    r = client.post("/api/cases", json={"pr_url": "short"})
    assert r.status_code == 422


def test_create_case_ingest_exception_returns_500(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.ingest_pr_url", side_effect=RuntimeError("DB error")):
        r = client.post(
            "/api/cases",
            json={"pr_url": "https://github.com/owner/repo/pull/99"},
        )
    assert r.status_code == 500


# ─── GET /api/cases ──────────────────────────────────────────────────────────


def test_list_cases_empty(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.list_cases", return_value=[]):
        r = client.get("/api/cases")

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["items"] == []


def test_list_cases_returns_items(client: TestClient) -> None:
    fake_item = {"case_id": CASE_ID, "status": "INGESTED"}
    with patch("kmp_repair_webapi.app.list_cases", return_value=[fake_item]):
        r = client.get("/api/cases")

    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["items"][0]["case_id"] == CASE_ID


def test_list_cases_filters_forwarded(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.list_cases", return_value=[]) as mock_lc:
        client.get("/api/cases?status=INGESTED&repo=myrepo&update_class=minor")

    kwargs = mock_lc.call_args.kwargs
    assert kwargs["status"] == "INGESTED"
    assert kwargs["repo"] == "myrepo"
    assert kwargs["update_class"] == "minor"


# ─── GET /api/cases/{case_id} ────────────────────────────────────────────────


def test_case_detail_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.get_case_detail", return_value=_fake_case_detail()):
        r = client.get(f"/api/cases/{CASE_ID}")

    assert r.status_code == 200
    assert r.json()["case"]["case_id"] == CASE_ID


def test_case_detail_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.get_case_detail", return_value=None):
        r = client.get("/api/cases/nonexistent")

    assert r.status_code == 404
    assert "no encontrado" in r.json()["detail"].lower()


# ─── GET /api/cases/{case_id}/history ────────────────────────────────────────


def test_case_history_found(client: TestClient) -> None:
    fake_history = {"case_id": CASE_ID, "transitions": [], "jobs": []}
    with (
        patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo,
        patch("kmp_repair_webapi.app.get_case_history", return_value=fake_history),
    ):
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        r = client.get(f"/api/cases/{CASE_ID}/history")

    assert r.status_code == 200
    assert r.json()["case_id"] == CASE_ID
    assert r.json()["transitions"] == []


def test_case_history_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        r = client.get("/api/cases/nonexistent/history")

    assert r.status_code == 404


# ─── POST /api/cases/{case_id}/jobs/stage ────────────────────────────────────


def test_run_stage_success(client: TestClient) -> None:
    fake_job = _fake_job()
    with (
        patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo,
        patch("kmp_repair_webapi.app.enqueue_stage_job") as mock_enqueue,
        patch("kmp_repair_webapi.app.serialize_job", return_value=fake_job),
    ):
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        mock_enqueue.return_value = MagicMock()

        r = client.post(
            f"/api/cases/{CASE_ID}/jobs/stage",
            json={"stage": "localize", "requested_by": "tester"},
        )

    assert r.status_code == 200
    assert r.json()["job"]["job_id"] == JOB_ID
    assert r.json()["job"]["stage"] == "localize"


def test_run_stage_case_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        r = client.post(
            "/api/cases/nonexistent/jobs/stage",
            json={"stage": "localize"},
        )

    assert r.status_code == 404


def test_run_stage_invalid_stage_returns_400(client: TestClient) -> None:
    with (
        patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo,
        patch(
            "kmp_repair_webapi.app.enqueue_stage_job",
            side_effect=ValueError("Parámetros no permitidos: {'bad_param'}"),
        ),
    ):
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        r = client.post(
            f"/api/cases/{CASE_ID}/jobs/stage",
            json={"stage": "localize", "params": {"bad_param": True}},
        )

    assert r.status_code == 400
    assert "Parámetros" in r.json()["detail"]


def test_run_stage_extra_field_rejected(client: TestClient) -> None:
    r = client.post(
        f"/api/cases/{CASE_ID}/jobs/stage",
        json={"stage": "localize", "extra_field": "oops"},
    )
    assert r.status_code == 422


# ─── POST /api/cases/{case_id}/jobs/pipeline ─────────────────────────────────


def test_run_pipeline_success(client: TestClient) -> None:
    fake_job = _fake_job(status="QUEUED")
    fake_job["job_type"] = "RUN_PIPELINE"
    with (
        patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo,
        patch("kmp_repair_webapi.app.enqueue_pipeline_job") as mock_enqueue,
        patch("kmp_repair_webapi.app.serialize_job", return_value=fake_job),
    ):
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        mock_enqueue.return_value = MagicMock()

        r = client.post(
            f"/api/cases/{CASE_ID}/jobs/pipeline",
            json={"start_from_stage": "localize", "requested_by": "tester"},
        )

    assert r.status_code == 200
    assert r.json()["job"]["job_type"] == "RUN_PIPELINE"
    assert r.json()["job"]["status"] == "QUEUED"


def test_run_pipeline_default_payload(client: TestClient) -> None:
    """Empty body is allowed — all fields have defaults."""
    fake_job = _fake_job()
    with (
        patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo,
        patch("kmp_repair_webapi.app.enqueue_pipeline_job") as mock_enqueue,
        patch("kmp_repair_webapi.app.serialize_job", return_value=fake_job),
    ):
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        mock_enqueue.return_value = MagicMock()
        r = client.post(f"/api/cases/{CASE_ID}/jobs/pipeline", json={})

    assert r.status_code == 200


def test_run_pipeline_case_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        r = client.post("/api/cases/nonexistent/jobs/pipeline", json={})

    assert r.status_code == 404


# ─── GET /api/jobs/{job_id} ──────────────────────────────────────────────────


def test_get_job_found(client: TestClient) -> None:
    fake_job = _fake_job()
    with (
        patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo,
        patch("kmp_repair_webapi.app.serialize_job", return_value=fake_job),
    ):
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        r = client.get(f"/api/jobs/{JOB_ID}")

    assert r.status_code == 200
    assert r.json()["job"]["job_id"] == JOB_ID


def test_get_job_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        r = client.get("/api/jobs/nonexistent")

    assert r.status_code == 404


# ─── POST /api/jobs/{job_id}/cancel ──────────────────────────────────────────


def test_cancel_job_success(client: TestClient) -> None:
    fake_job = _fake_job(status="CANCEL_REQUESTED")
    with (
        patch("kmp_repair_webapi.app.request_job_cancel") as mock_cancel,
        patch("kmp_repair_webapi.app.serialize_job", return_value=fake_job),
    ):
        mock_cancel.return_value = MagicMock()
        r = client.post(f"/api/jobs/{JOB_ID}/cancel", json={})

    assert r.status_code == 200
    assert r.json()["job"]["status"] == "CANCEL_REQUESTED"


def test_cancel_job_not_found(client: TestClient) -> None:
    with patch(
        "kmp_repair_webapi.app.request_job_cancel",
        side_effect=ValueError("Job nonexistent no existe"),
    ):
        r = client.post("/api/jobs/nonexistent/cancel", json={})

    assert r.status_code == 404
    assert "no existe" in r.json()["detail"]


def test_cancel_job_optional_requested_by(client: TestClient) -> None:
    fake_job = _fake_job(status="CANCEL_REQUESTED")
    with (
        patch("kmp_repair_webapi.app.request_job_cancel") as mock_cancel,
        patch("kmp_repair_webapi.app.serialize_job", return_value=fake_job),
    ):
        mock_cancel.return_value = MagicMock()
        r = client.post(
            f"/api/jobs/{JOB_ID}/cancel",
            json={"requested_by": "frontend"},
        )

    assert r.status_code == 200


# ─── GET /api/jobs/{job_id}/logs ─────────────────────────────────────────────


def test_job_logs_found(client: TestClient) -> None:
    job_mock = MagicMock()
    job_mock.status = "RUNNING"
    job_mock.current_stage = "localize"
    job_mock.log_path = "/tmp/fake-job.log"

    with (
        patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo,
        patch("kmp_repair_webapi.app.read_tail_lines", return_value=["line 1", "line 2"]),
    ):
        MockRepo.return_value.get_by_id.return_value = job_mock
        r = client.get(f"/api/jobs/{JOB_ID}/logs")

    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == JOB_ID
    assert body["status"] == "RUNNING"
    assert body["current_stage"] == "localize"
    assert body["lines"] == ["line 1", "line 2"]


def test_job_logs_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        r = client.get(f"/api/jobs/nonexistent/logs")

    assert r.status_code == 404


def test_job_logs_tail_too_small_rejected(client: TestClient) -> None:
    """tail < 20 must be rejected by FastAPI query validation."""
    with patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        r = client.get(f"/api/jobs/{JOB_ID}/logs?tail=5")

    assert r.status_code == 422


def test_job_logs_tail_too_large_rejected(client: TestClient) -> None:
    """tail > 2000 must be rejected."""
    with patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = MagicMock()
        r = client.get(f"/api/jobs/{JOB_ID}/logs?tail=9999")

    assert r.status_code == 422


def test_job_logs_default_tail(client: TestClient) -> None:
    """Default tail (200) should pass validation."""
    job_mock = MagicMock()
    job_mock.status = "SUCCEEDED"
    job_mock.current_stage = None
    job_mock.log_path = None

    with (
        patch("kmp_repair_webapi.app.PipelineJobRepo") as MockRepo,
        patch("kmp_repair_webapi.app.read_tail_lines", return_value=[]) as mock_read,
    ):
        MockRepo.return_value.get_by_id.return_value = job_mock
        r = client.get(f"/api/jobs/{JOB_ID}/logs")

    assert r.status_code == 200
    mock_read.assert_called_once_with(None, lines=200)


# ─── GET /api/jobs/{job_id}/stream ───────────────────────────────────────────


def test_job_stream_returns_sse(client: TestClient) -> None:
    fake_job = _fake_job(status="SUCCEEDED")

    def fake_generator(job_id: str):
        yield f"event: status\ndata: {{}}\n\n"
        yield f"event: done\ndata: {{}}\n\n"

    with patch("kmp_repair_webapi.app._job_stream_generator", side_effect=fake_generator):
        r = client.get(f"/api/jobs/{JOB_ID}/stream")

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: status" in r.text
    assert "event: done" in r.text


def test_job_stream_passes_job_id(client: TestClient) -> None:
    """Verify the correct job_id is forwarded to the generator."""

    captured: list[str] = []

    def fake_generator(job_id: str):
        captured.append(job_id)
        yield "event: done\ndata: {}\n\n"

    with patch("kmp_repair_webapi.app._job_stream_generator", side_effect=fake_generator):
        client.get(f"/api/jobs/{JOB_ID}/stream")

    assert captured == [JOB_ID]


# ─── GET /api/stream/active ───────────────────────────────────────────────────


def test_active_jobs_stream_returns_sse(client: TestClient) -> None:
    def fake_generator():
        yield 'event: active\ndata: []\n\n'

    with patch("kmp_repair_webapi.app._active_jobs_stream_generator", side_effect=fake_generator):
        r = client.get("/api/stream/active")

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: active" in r.text


# ─── GET /api/cases/{case_id}/artifact-content ───────────────────────────────


def test_artifact_content_success(client: TestClient, tmp_path) -> None:
    artifact_dir = tmp_path / "case-dir"
    artifact_dir.mkdir()
    (artifact_dir / "output.txt").write_text("hello artifact")

    case_mock = MagicMock()
    case_mock.artifact_dir = str(artifact_dir)

    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = case_mock
        r = client.get(
            f"/api/cases/{CASE_ID}/artifact-content",
            params={"path": "output.txt"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == CASE_ID
    assert body["content"] == "hello artifact"
    assert body["truncated"] is False


def test_artifact_content_truncated(client: TestClient, tmp_path) -> None:
    artifact_dir = tmp_path / "case-dir"
    artifact_dir.mkdir()
    # Write content larger than min max_bytes (1000 bytes via param)
    big_content = "x" * 2000
    (artifact_dir / "big.txt").write_text(big_content)

    case_mock = MagicMock()
    case_mock.artifact_dir = str(artifact_dir)

    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = case_mock
        r = client.get(
            f"/api/cases/{CASE_ID}/artifact-content",
            params={"path": "big.txt", "max_bytes": 1000},
        )

    assert r.status_code == 200
    assert r.json()["truncated"] is True
    assert "[truncated]" in r.json()["content"]


def test_artifact_content_case_not_found(client: TestClient) -> None:
    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = None
        r = client.get(f"/api/cases/nonexistent/artifact-content?path=x.txt")

    assert r.status_code == 404


def test_artifact_content_file_not_found(client: TestClient, tmp_path) -> None:
    artifact_dir = tmp_path / "case-dir"
    artifact_dir.mkdir()

    case_mock = MagicMock()
    case_mock.artifact_dir = str(artifact_dir)

    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = case_mock
        r = client.get(
            f"/api/cases/{CASE_ID}/artifact-content",
            params={"path": "does_not_exist.txt"},
        )

    assert r.status_code == 404


def test_artifact_content_path_traversal_blocked(client: TestClient, tmp_path) -> None:
    """../../ traversal outside artifact_dir must return 400."""
    artifact_dir = tmp_path / "case-dir"
    artifact_dir.mkdir()

    case_mock = MagicMock()
    case_mock.artifact_dir = str(artifact_dir)

    with patch("kmp_repair_webapi.app.RepairCaseRepo") as MockRepo:
        MockRepo.return_value.get_by_id.return_value = case_mock
        r = client.get(
            f"/api/cases/{CASE_ID}/artifact-content",
            params={"path": "../../etc/passwd"},
        )

    assert r.status_code == 400


def test_artifact_content_max_bytes_too_small_rejected(client: TestClient) -> None:
    """max_bytes < 1000 must be rejected by query validation."""
    r = client.get(
        f"/api/cases/{CASE_ID}/artifact-content",
        params={"path": "x.txt", "max_bytes": 100},
    )
    assert r.status_code == 422


# ─── GET /api/reports/compare ────────────────────────────────────────────────


def test_compare_reports_empty(client: TestClient, mock_session: MagicMock) -> None:
    mock_session.scalars.return_value.all.return_value = []

    r = client.get("/api/reports/compare")

    assert r.status_code == 200
    body = r.json()
    assert body["comparison"] == []
    assert body["rows"] == 0


def test_compare_reports_aggregates_metrics(client: TestClient, mock_session: MagicMock) -> None:
    metric = MagicMock()
    metric.repair_mode = "full_thesis"
    metric.bsr = 1.0
    metric.ctsr = 0.9
    metric.ffsr = 0.8
    metric.efr = 0.7
    metric.hit_at_1 = 1.0
    metric.hit_at_3 = 1.0
    metric.hit_at_5 = 1.0
    metric.source_set_accuracy = 0.95
    mock_session.scalars.return_value.all.return_value = [metric]

    r = client.get("/api/reports/compare?modes=full_thesis")

    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == 1
    row = body["comparison"][0]
    assert row["repair_mode"] == "full_thesis"
    assert row["bsr"] == 1.0
    assert row["ctsr"] == 0.9
    assert row["cases"] == 1


def test_compare_reports_multiple_modes(client: TestClient, mock_session: MagicMock) -> None:
    def make_metric(mode: str, bsr: float) -> MagicMock:
        m = MagicMock()
        m.repair_mode = mode
        m.bsr = bsr
        m.ctsr = m.ffsr = m.efr = None
        m.hit_at_1 = m.hit_at_3 = m.hit_at_5 = None
        m.source_set_accuracy = None
        return m

    mock_session.scalars.return_value.all.return_value = [
        make_metric("full_thesis", 1.0),
        make_metric("raw_error", 0.5),
    ]

    r = client.get("/api/reports/compare")

    assert r.status_code == 200
    modes = {row["repair_mode"] for row in r.json()["comparison"]}
    assert modes == {"full_thesis", "raw_error"}
    assert r.json()["rows"] == 2


def test_compare_reports_case_filter(client: TestClient, mock_session: MagicMock) -> None:
    """case_id query param is forwarded to the DB query (no exception)."""
    mock_session.scalars.return_value.all.return_value = []

    r = client.get(f"/api/reports/compare?case_id={CASE_ID}")

    assert r.status_code == 200
    assert r.json()["rows"] == 0
