from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kmp_repair_pipeline.webapi.app import create_app
from kmp_repair_pipeline.webapi.orchestrator import _run_stage_impl
from kmp_repair_pipeline.webapi.stages import (
    command_for_stage,
    sanitize_pipeline_request,
    sanitize_stage_params,
)


def test_sanitize_stage_params_repair_allowlist() -> None:
    params = sanitize_stage_params(
        "repair",
        {
            "mode": "context_rich",
            "top_k": 7,
            "patch_strategy": "single_diff",
            "force_patch_attempt": True,
        },
    )
    assert params["mode"] == "context_rich"
    assert params["top_k"] == 7


def test_sanitize_stage_params_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="Parámetros no permitidos"):
        sanitize_stage_params("validate", {"oops": True})


def test_sanitize_pipeline_request_defaults_and_scope() -> None:
    start, normalized = sanitize_pipeline_request(
        "localize",
        {
            "localize": {"top_k": 5},
            "repair": {"mode": "raw_error"},
        },
    )
    assert start == "localize"
    assert "localize" in normalized
    assert normalized["localize"]["top_k"] == 5
    assert normalized["repair"]["mode"] == "raw_error"


def test_command_for_stage_localize_preview() -> None:
    cmd = command_for_stage(
        "1234",
        "localize",
        {"top_k": 10, "no_agent": False, "provider": None, "model": None, "artifact_base": "data/artifacts"},
    )
    assert "kmp-repair localize 1234" in cmd


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


@dataclass
class _FakeMetric:
    case_id: str
    repair_mode: str
    bsr: float


def test_metrics_stage_serializes_dataclass_metrics() -> None:
    session = MagicMock()
    fake_result = MagicMock()
    fake_result.metrics = [
        _FakeMetric(case_id="case-1", repair_mode="full_thesis", bsr=1.0)
    ]

    with patch("kmp_repair_pipeline.webapi.orchestrator.evaluate", return_value=fake_result):
        summary = _run_stage_impl(
            session=session,
            case_id="case-1",
            stage="metrics",
            params={},
        )

    assert summary["metric_count"] == 1
    assert summary["metrics"][0] == {
        "case_id": "case-1",
        "repair_mode": "full_thesis",
        "bsr": 1.0,
    }
