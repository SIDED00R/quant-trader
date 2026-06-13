"""PostgreSQL 연결 풀 (단일 책임: Postgres 연결)."""
from psycopg_pool import ConnectionPool

from common.config import POSTGRES_DSN

pool = ConnectionPool(POSTGRES_DSN, min_size=1, max_size=5, open=False)


def open_pool() -> None:
    pool.open()


def close_pool() -> None:
    pool.close()
