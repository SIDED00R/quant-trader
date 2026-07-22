"""기동 시 DB SQL 적용 (단일 책임: 버전 마이그레이션 순차 적용 + 부팅 repair 재실행).

파일 규약: db/migrations/{postgres,clickhouse}/NNNN_설명.sql (NNNN=4자리 순번).
  - 같은 DB에서 순번이 중복되거나 규약을 어긴 .sql 파일명이 있으면 기동 실패(ValueError).
문장 규약: 문장 하나당 세미콜론 1개, 문자열/주석 내 세미콜론 금지(단순 `;` split 파서).

Postgres 마이그레이션은 파일 단위 트랜잭션으로 적용(문장들 + 이력 기록이 원자). 어느 문장이 실패하면
그 파일은 롤백되고 예외가 전파돼 이력에 남지 않는다 → 다음 부팅에 재시도.
ClickHouse는 트랜잭션이 없어 실패 시 부분 적용될 수 있다 → CH 마이그레이션은 반드시 멱등
(IF NOT EXISTS / ADD COLUMN IF NOT EXISTS 등)으로 작성한다. 이력은 파일의 전 문장 성공 후에만 기록.

repair(db/postgres_repair.sql)는 버전 이력과 무관하게 매 부팅 재실행되는 멱등 자가회복 문장이다
(마이그레이션이 아니라 데이터 백필 — 자세한 이유는 해당 파일 주석 참조).

down(롤백) 마이그레이션은 두지 않는다 — forward-only. 잘못은 다음 순번으로 정정(fix-forward).
동시성 락은 불요 — 두 VM은 각자 로컬 DB이고 db-init은 부팅당 1회 단일 컨테이너라 경합이 없다.
"""
import re
from pathlib import Path

_DB_DIR = Path(__file__).resolve().parents[1] / "db"
MIGRATIONS_DIR = _DB_DIR / "migrations"
POSTGRES_REPAIR = _DB_DIR / "postgres_repair.sql"
_FILE_RE = re.compile(r"^(\d{4})_\w+\.sql$")


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def _migration_files(sub: str) -> list[tuple[int, Path]]:
    """db/migrations/<sub>/ 의 마이그레이션 파일을 (버전, 경로) 오름차순으로. 규약 위반은 ValueError."""
    d = MIGRATIONS_DIR / sub
    versions: dict[int, Path] = {}
    for path in sorted(d.glob("*.sql")):
        m = _FILE_RE.match(path.name)
        if not m:
            raise ValueError(f"마이그레이션 파일명 규약 위반: {path.name} (NNNN_설명.sql 이어야 함)")
        ver = int(m.group(1))
        if ver in versions:
            raise ValueError(f"마이그레이션 순번 중복: {ver:04d} ({versions[ver].name}, {path.name})")
        versions[ver] = path
    return sorted(versions.items())


def apply_postgres(conn) -> list[str]:
    """미적용 postgres 마이그레이션을 순서대로 적용. 적용한 파일명 리스트 반환."""
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version    INTEGER PRIMARY KEY,
        filename   TEXT NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    applied = []
    for ver, path in _migration_files("postgres"):
        if ver in done:
            continue
        with conn.transaction():
            for stmt in _statements(path.read_text(encoding="utf-8")):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)", (ver, path.name))
        applied.append(path.name)
    return applied


def apply_postgres_repair(conn) -> None:
    """매 부팅 멱등 재실행되는 자가회복 문장(마이그레이션 이력에 남기지 않음)."""
    with conn.transaction():
        for stmt in _statements(POSTGRES_REPAIR.read_text(encoding="utf-8")):
            conn.execute(stmt)


def apply_clickhouse(client) -> list[str]:
    """미적용 clickhouse 마이그레이션을 순서대로 적용. 적용한 파일명 리스트 반환."""
    client.command("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version    UInt32,
        filename   String,
        applied_at DateTime64(3, 'UTC') DEFAULT now64(3))
        ENGINE = ReplacingMergeTree(applied_at) ORDER BY version""")
    done = {r[0] for r in client.query("SELECT DISTINCT version FROM schema_migrations").result_rows}
    applied = []
    for ver, path in _migration_files("clickhouse"):
        if ver in done:
            continue
        for stmt in _statements(path.read_text(encoding="utf-8")):
            client.command(stmt)
        client.insert("schema_migrations", [[ver, path.name]], column_names=["version", "filename"])
        applied.append(path.name)
    return applied
