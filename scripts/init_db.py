"""DB 스키마 1회 적용 (단일 책임: 기동 전 스키마 마이그레이션).

앱 런타임 경로에서 DDL을 분리한다. 인프라 기동 후 한 번 실행:
    python -m scripts.init_db

db/migrations/{postgres,clickhouse}/의 미적용 마이그레이션만 순서대로 적용하고(이미 적용분 skip),
postgres repair(자가회복 백필)는 매번 재실행한다.
"""
from common import migrations
from common.clickhouse_client import create_client
from common.postgres_client import close_pool, open_pool, pool


def main() -> None:
    open_pool()
    try:
        with pool.connection() as conn:
            applied = migrations.apply_postgres(conn)
            migrations.apply_postgres_repair(conn)
        print(f"[init_db] postgres migrations: {applied or '(up-to-date)'}")
    finally:
        close_pool()

    applied = migrations.apply_clickhouse(create_client())
    print(f"[init_db] clickhouse migrations: {applied or '(up-to-date)'}")


if __name__ == "__main__":
    main()
