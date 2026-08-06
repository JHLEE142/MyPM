from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pacepm.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    schema = inspect(engine)
    if "projects" in schema.get_table_names() and "owner" not in {
        column["name"] for column in schema.get_columns("projects")
    }:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE projects ADD COLUMN owner VARCHAR(100)"))
    if (
        engine.dialect.name == "sqlite"
        and "tasks" in schema.get_table_names()
        and "cadence" not in {column["name"] for column in schema.get_columns("tasks")}
    ):
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tasks ADD COLUMN cadence VARCHAR(10)"))
    if "tasks" in schema.get_table_names() and "sort_order" not in {
        column["name"] for column in schema.get_columns("tasks")
    }:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tasks ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))
            # 기존 데이터는 id 순서를 그대로 보존
            connection.execute(text("UPDATE tasks SET sort_order = id WHERE sort_order = 0"))
    # Stage D 초기 스키마의 ai_usage.success NOT NULL 잔재 보정 — 쿼터 예약 행은 success=NULL로 삽입된다.
    # 사용량 계측 테이블이라 재생성으로 해소(데이터 손실 허용).
    if "ai_usage" in schema.get_table_names():
        success_column = next(
            (column for column in schema.get_columns("ai_usage") if column["name"] == "success"), None
        )
        if success_column is not None and not success_column.get("nullable", True):
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE ai_usage"))
            Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
