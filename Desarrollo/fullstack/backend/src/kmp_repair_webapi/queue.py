"""RQ queue helpers."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis
from rq import Queue

from .settings import get_settings


@lru_cache(maxsize=1)
def get_redis_connection() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url)


@lru_cache(maxsize=1)
def get_queue() -> Queue:
    settings = get_settings()
    return Queue(
        name=settings.queue_name,
        connection=get_redis_connection(),
        default_timeout=settings.queue_default_timeout_s,
    )
