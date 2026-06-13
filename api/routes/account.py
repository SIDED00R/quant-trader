"""계좌 조회 라우트 (단일 책임: 잔고/포지션 조회)."""
from fastapi import APIRouter, HTTPException

from common.postgres_client import pool

router = APIRouter()


@router.get("/accounts/{account_id}")
def get_account(account_id: str):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT account_id, krw_balance FROM accounts WHERE account_id=%s",
            (account_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "account not found")
        positions = conn.execute(
            "SELECT symbol, quantity, avg_buy_price FROM positions "
            "WHERE account_id=%s AND quantity <> 0 ORDER BY symbol",
            (account_id,),
        ).fetchall()

    return {
        "account_id": row[0],
        "krw_balance": float(row[1]),
        "positions": [
            {"symbol": p[0], "quantity": float(p[1]), "avg_buy_price": float(p[2])}
            for p in positions
        ],
    }
