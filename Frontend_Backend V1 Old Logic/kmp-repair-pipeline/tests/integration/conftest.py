"""Integration test fixtures — require a live PostgreSQL database.

Run integration tests with:
    pytest tests/integration/ -v

These tests are skipped if the database is not reachable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from kmp_repair_pipeline.storage.db import get_db_url
from kmp_repair_pipeline.storage.models import Base


def _db_available() -> bool:
    try:
        engine = create_engine(get_db_url(), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(),
    reason="PostgreSQL not reachable — start with: docker compose up -d postgres",
)


@pytest.fixture(scope="session")
def db_engine():
    """Session-scoped engine connected to the test database."""
    engine = create_engine(get_db_url(), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Function-scoped session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, expire_on_commit=False)
    session = Session()

    yield session

    session.close()
    try:
        transaction.rollback()
    except Exception:
        pass  # transaction may be deassociated when a nested rollback already happened
    connection.close()
