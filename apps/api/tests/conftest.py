from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="pacepm-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["STORAGE_PATH"] = str(TEST_ROOT / "storage")
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["AI_ROUTER_ORDER"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def project_payload():
    return {
        "name": "PacePM test",
        "start_date": "2099-01-05",
        "target_date": "2099-02-27",
        "work_days": [0, 1, 2, 3, 4],
        "daily_capacity_hours": 4,
        "buffer_ratio": 0.2,
    }
