"""PostgreSQL 연결 풀 + 스키마 적용 (단일 책임: Postgres 연결)."""
from pathlib import Path

from psycopg_pool import ConnectionPool

from common.config import POSTGRES_DSN

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "postgres_schema.sql"

pool = ConnectionPool(POSTGRES_DSN, min_size=1, max_size=5, open=False)


def open_pool() -> None:
    pool.open()


def close_pool() -> None:
    pool.close()


def ensure_schema() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with pool.connection() as conn:
        for stmt in (s.strip() for s in sql.split(";")):
            if stmt:
                conn.execute(stmt)
