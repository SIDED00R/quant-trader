"""테스트 공용 헬퍼 (단일 책임: 가짜 DB 리소스 — DB/네트워크 무접촉 단위테스트용)."""
from unittest.mock import MagicMock


def fake_pool():
    """psycopg pool 대역: `with pool.connection() as conn:` 컨텍스트가 가짜 conn을 돌려준다.

    반환 (pool, conn). conn.execute/transaction 등은 MagicMock이라 SQL·파라미터를 call_args로 검사한다.
    fetchone/fetchall 결과가 필요하면 호출부에서 conn.execute.return_value.fetchone.return_value 등을 설정.
    """
    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    pool.connection.return_value.__exit__.return_value = False
    return pool, conn
