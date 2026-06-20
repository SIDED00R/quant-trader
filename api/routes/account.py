"""계좌 조회 라우트 (단일 책임: 잔고/포지션 조회).

계정은 항상 로그인 세션에서 결정한다(요청 파라미터로 타인 계정 조회 불가).
"""
from fastapi import APIRouter, Depends, HTTPException

from api.security import current_account_id
from common.config import INITIAL_BALANCE
from common.postgres_client import pool

router = APIRouter()


@router.get("/account")
def get_account(account_id: str = Depends(current_account_id)):
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
        # 누적 수수료(매수·매도 전체) — 순손익엔 이미 반영돼 있고, 별도 표시용
        total_fees = conn.execute(
            "SELECT COALESCE(SUM(fee), 0) FROM executions WHERE account_id=%s",
            (account_id,),
        ).fetchone()[0]

    return {
        "account_id": row[0],
        "krw_balance": float(row[1]),
        "initial_balance": float(INITIAL_BALANCE),   # 순손익 기준선(평가자산 − 초기자본 = 수수료 포함 순손익)
        "total_fees": float(total_fees),             # 누적 지불 수수료
        "positions": [
            {"symbol": p[0], "quantity": float(p[1]), "avg_buy_price": float(p[2])}
            for p in positions
        ],
    }
