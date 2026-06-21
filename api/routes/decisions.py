"""매매 결정 기록 조회 라우트 (단일 책임: trade_once 결정·근거 조회).

trade_once가 매 실행마다 남긴 종목별 결정(매매/유지·수량·금액·사유·부하별 근거)을 최신순으로 돌려준다.
시각은 UTC(timestamptz) ISO 문자열로 반환하고, KST 변환은 화면에서 수행한다(history 라우트와 동일 규약).
"""
from fastapi import APIRouter, Depends

from api.security import current_account_id
from common.postgres_client import pool

router = APIRouter(prefix="/decisions")


def _limit(n: int) -> int:
    return max(1, min(n, 200))


@router.get("")
def decisions(account_id: str = Depends(current_account_id), limit: int = 100):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT run_ts, bar_date, symbol, price, target_weight, action, quantity, "
            "amount_krw, equity, reason, signals, executed "
            "FROM trade_decisions WHERE account_id=%s ORDER BY run_ts DESC, symbol LIMIT %s",
            (account_id, _limit(limit)),
        ).fetchall()
    return [
        {
            "run_ts": r[0].isoformat(),
            "bar_date": r[1].isoformat() if r[1] is not None else None,
            "symbol": r[2],
            "price": float(r[3]) if r[3] is not None else None,
            "target_weight": float(r[4]) if r[4] is not None else None,
            "action": r[5],
            "quantity": float(r[6]) if r[6] is not None else None,
            "amount_krw": float(r[7]) if r[7] is not None else None,
            "equity": float(r[8]) if r[8] is not None else None,
            "reason": r[9],
            "signals": r[10],          # JSONB → psycopg가 파이썬 객체로 복원
            "executed": r[11],
        }
        for r in rows
    ]
