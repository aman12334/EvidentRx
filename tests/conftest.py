"""
Shared pytest fixtures for the EvidentRx test suite.

Boot order (all happens before any test module is imported):
  1. Set required env vars so app.config / pydantic-settings don't raise.
  2. Patch sqlalchemy.create_engine so that app.database can be imported
     without psycopg2 being installed (unit tests never hit the real DB).
     In CI the full environment has psycopg2; the patch is still harmless.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

# ── 1. Environment bootstrap ──────────────────────────────────────────────────
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://evidentrx:evidentrx@localhost:5432/evidentrx_test",
)
os.environ.setdefault("JWT_SECRET_KEY", "test_secret_key_for_ci_only_not_production_32c")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

# ── 2. Prevent psycopg2 import at module-load time ────────────────────────────
# app/database.py calls create_engine(...) at the top level, which triggers
# psycopg2 import even before any test runs.  We patch create_engine globally
# so that unit tests can import router modules without a live database driver.
_mock_engine = MagicMock()
_mock_engine.connect.return_value.__enter__ = lambda s: MagicMock()
_mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
_engine_patch = patch("sqlalchemy.create_engine", return_value=_mock_engine)
_engine_patch.start()

# ── Imports ───────────────────────────────────────────────────────────────────
from unittest.mock import MagicMock

import pytest

# ── DB mock fixture ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_db() -> MagicMock:
    """
    Return a MagicMock that quacks like a SQLAlchemy Session.

    Pre-wired execute chain:
        db.execute(...).fetchone()               → None
        db.execute(...).scalar()                 → None
        db.execute(...).fetchall()               → []
        db.execute(...).mappings().fetchall()    → []
        db.execute(...).mappings().fetchone()    → None
    """
    session = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = None
    result.scalar.return_value = None
    result.fetchall.return_value = []
    result.mappings.return_value.fetchall.return_value = []
    result.mappings.return_value.fetchone.return_value = None
    session.execute.return_value = result
    return session


# ── FastAPI test-client factory ───────────────────────────────────────────────

@pytest.fixture
def api_client(mock_db: MagicMock):
    """
    Return a FastAPI TestClient with the real DB dependency swapped out
    for the in-memory mock session.  All imports are deferred so the env
    vars set above are in place before SQLAlchemy tries to create the engine.

    Requires fastapi + the project to be installed (pip install -e ".[dev]").
    """
    # Deferred imports — not needed for unit tests that only use mock_db
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from api.main import app
    from app.database import get_db

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
