"""ClickHouse 클라이언트 + 스키마 적용 (단일 책임: ClickHouse 연결)."""
from pathlib import Path

import clickhouse_connect

from common.config import (
    CLICKHOUSE_DB,
    CLICKHOUSE_HOST,
    CLICKHOUSE_HTTP_PORT,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_USER,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "clickhouse_schema.sql"


def create_client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_HTTP_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DB,
    )


def ensure_schema(client) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    for stmt in (s.strip() for s in sql.split(";")):
        if stmt:
            client.command(stmt)
