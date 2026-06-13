"""DB 스키마 1회 적용 (단일 책임: 기동 전 스키마 마이그레이션).

앱 런타임 경로에서 DDL을 분리한다. 인프라 기동 후 한 번 실행:
    python -m scripts.init_db
"""
from common.clickhouse_client import create_client
from common.postgres_client import close_pool, open_pool, pool
from common.schema_loader import apply_clickhouse_schema, apply_postgres_schema


def main() -> None:
    open_pool()
    try:
        with pool.connection() as conn:
            apply_postgres_schema(conn)
        print("[init_db] postgres schema applied")
    finally:
        close_pool()

    apply_clickhouse_schema(create_client())
    print("[init_db] clickhouse schema applied")


if __name__ == "__main__":
    main()
