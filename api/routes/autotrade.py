"""자동매매 토글 라우트 (단일 책임: 계정별 auto_trade 플래그 조회/설정).

봇은 auto_trade=TRUE 인 계정에 대해서만 매매한다(trading/strategy/runners/trade_once.py·commander.py).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.security import current_account_id
from common.postgres_client import pool

router = APIRouter(prefix="/autotrade")


class Toggle(BaseModel):
    enabled: bool


@router.get("")
def get_autotrade(account_id: str = Depends(current_account_id)):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT auto_trade FROM accounts WHERE account_id=%s", (account_id,)
        ).fetchone()
    return {"enabled": bool(row[0]) if row else False}


@router.post("")
def set_autotrade(body: Toggle, account_id: str = Depends(current_account_id)):
    with pool.connection() as conn:
        conn.execute(
            "UPDATE accounts SET auto_trade=%s WHERE account_id=%s",
            (body.enabled, account_id),
        )
    return {"enabled": body.enabled}
