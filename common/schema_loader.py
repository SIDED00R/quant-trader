"""스키마 적용 (단일 책임: .sql 파일을 DB에 적용).

연결(클라이언트)과 분리된 1회성 스키마 적용 책임. scripts/init_db.py에서 사용.
스키마 파일은 '문장 하나당 세미콜론, 문자열/달러쿼팅 내 세미콜론 없음' 규약을 따른다.
"""
from pathlib import Path

DB_DIR = Path(__file__).resolve().parents[1] / "db"
POSTGRES_SCHEMA = DB_DIR / "postgres_schema.sql"
CLICKHOUSE_SCHEMA = DB_DIR / "clickhouse_schema.sql"


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def apply_postgres_schema(conn) -> None:
    """단일 트랜잭션으로 원자 적용."""
    with conn.transaction():
        for stmt in _statements(POSTGRES_SCHEMA.read_text(encoding="utf-8")):
            conn.execute(stmt)


def apply_clickhouse_schema(client) -> None:
    for stmt in _statements(CLICKHOUSE_SCHEMA.read_text(encoding="utf-8")):
        client.command(stmt)
