"""Runtime settings for FastAPI, RQ, and web orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class WebSettings:
    api_host: str
    api_port: int
    redis_url: str
    queue_name: str
    queue_default_timeout_s: int
    artifact_base: Path
    report_output_dir: Path
    cors_origins: list[str]


@lru_cache(maxsize=1)
def get_settings() -> WebSettings:
    cors_raw = os.environ.get("KMP_WEB_CORS_ORIGINS", "http://localhost:3000")
    cors_origins = [v.strip() for v in cors_raw.split(",") if v.strip()]

    return WebSettings(
        api_host=os.environ.get("KMP_WEB_API_HOST", "0.0.0.0"),
        api_port=int(os.environ.get("KMP_WEB_API_PORT", "8000")),
        redis_url=os.environ.get("KMP_REDIS_URL", "redis://localhost:6379/0"),
        queue_name=os.environ.get("KMP_RQ_QUEUE", "kmp_pipeline"),
        queue_default_timeout_s=int(os.environ.get("KMP_RQ_DEFAULT_TIMEOUT_S", "10800")),
        artifact_base=Path(os.environ.get("KMP_ARTIFACT_BASE", "data/artifacts")).resolve(),
        report_output_dir=Path(os.environ.get("KMP_REPORT_OUTPUT_DIR", "data/reports")).resolve(),
        cors_origins=cors_origins,
    )
