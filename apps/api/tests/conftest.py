from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="mypm-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["STORAGE_PATH"] = str(TEST_ROOT / "storage")
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)
os.environ["AI_ROUTER_ORDER"] = "mock"
os.environ["AI_ANALYSIS_ROUTER_ORDER"] = "mock"
# 기존 검토 게이트 플로우 테스트 보존용 — 런타임 기본은 자동 승인
os.environ["AI_REVIEW_GATE"] = "1"

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
def db_session():
    """API를 거치지 않고 직접 조회·조작할 때 쓰는 세션."""
    from app.database import SessionLocal

    with SessionLocal() as session:
        yield session


@pytest.fixture
def project_payload():
    return {
        "name": "MyPM test",
        "start_date": "2099-01-05",
        "target_date": "2099-02-27",
        "work_days": [0, 1, 2, 3, 4],
        "daily_capacity_hours": 4,
        "buffer_ratio": 0.2,
    }
