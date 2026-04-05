"""Database engine and session factory.

Usage:
    from kmp_repair_pipeline.storage.db import get_session, engine

    with get_session() as session:
        session.add(...)
        session.commit()
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ..utils.log import get_logger

log = get_logger(__name__)

_DEFAULT_URL = "postgresql+psycopg2://kmp_repair:kmp_repair_dev@localhost:5432/kmp_repair"

_engine = None
_SessionFactory = None


def get_db_url() -> str:
    return (
        os.environ.get("KMP_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_URL
    )


def get_engine():
    global _engine
    if _engine is None:
        url = get_db_url()
        _engine = create_engine(url, pool_pre_ping=True, echo=False)
        log.info(f"Database engine created: {url.split('@')[-1]}")
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager that yields a Session and commits on clean exit."""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> tuple[bool, str]:
    """Return (ok, detail) — used by the doctor command."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
        return True, str(version)
    except Exception as exc:
        return False, str(exc)


def dispose_engine() -> None:
    """Dispose the engine — useful in tests to reset state."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionFactory = None
