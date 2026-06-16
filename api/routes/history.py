"""거래 내역 조회 라우트 (단일 책임: 주문/체결 내역 조회).

시각은 UTC(timestamptz) ISO 문자열로 반환하고, KST 변환은 화면에서 수행한다.
"""
from fastapi import APIRouter, Depends

from api.security import current_account_id
from common.postgres_client import pool

router = APIRouter(prefix="/history")


def _limit(n: int) -> int:
    return max(1, min(n, 100))


@router.get("/orders")
def orders(account_id: str = Depends(current_account_id), limit: int = 20):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT order_id, symbol, side, type, price, quantity, status, created_at "
            "FROM orders WHERE account_id=%s ORDER BY created_at DESC LIMIT %s",
            (account_id, _limit(limit)),
        ).fetchall()
    return [
        {
            "order_id": str(r[0]),
            "symbol": r[1],
            "side": r[2],
            "type": r[3],
            "price": float(r[4]) if r[4] is not None else None,
            "quantity": float(r[5]),
            "status": r[6],
            "ts": r[7].isoformat(),
        }
        for r in rows
    ]


@router.get("/executions")
def executions(account_id: str = Depends(current_account_id), limit: int = 20):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT execution_id, symbol, side, price, quantity, fee, executed_at "
            "FROM executions WHERE account_id=%s ORDER BY executed_at DESC LIMIT %s",
            (account_id, _limit(limit)),
        ).fetchall()
    return [
        {
            "execution_id": str(r[0]),
            "symbol": r[1],
            "side": r[2],
            "price": float(r[3]),
            "quantity": float(r[4]),
            "fee": float(r[5]),
            "ts": r[6].isoformat(),
        }
        for r in rows
    ]
