"""Pydantic request/response schemas for the web API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_url: str = Field(min_length=10)
    artifact_dir: str | None = None
    detection_source: str = "dependabot"


class RunStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    requested_by: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class RunPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_from_stage: str | None = None
    requested_by: str | None = None
    params_by_stage: dict[str, dict[str, Any]] = Field(default_factory=dict)


class CancelJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_by: str | None = None


class ArtifactContentResponse(BaseModel):
    path: str
    content: str
    truncated: bool


class JobResponse(BaseModel):
    job_id: str
    case_id: str
    job_type: str
    stage: str | None
    start_from_stage: str | None
    status: str
    rq_job_id: str | None
    requested_by: str | None
    command_preview: str | None
    params: dict[str, Any] | None
    effective_params: dict[str, Any] | None
    cancel_requested: bool
    current_stage: str | None
    error_message: str | None
    result_summary: dict[str, Any] | None
    log_path: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
