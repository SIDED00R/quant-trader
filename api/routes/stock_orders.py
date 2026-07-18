"""수동 주식 주문 라우트 (단일 책임: 즉시/예약 주식 주문 접수·조회·취소).

접수만 담당 — 실행은 api.stock_order_executor(lifespan 백그라운드)가 scheduled_at 도래 시
common.broker.kis_chase.place_and_chase로 수행한다. 시장시간은 검증하지 않는다(장외면 KIS 거부가
detail에 남고 상태 FAILED). scheduled_at: naive 입력은 KST로 간주, 미지정 = 즉시(now).
"""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from api.security import current_account_id
from common.postgres_client import pool

router = APIRouter(prefix="/stocks")

_KST = ZoneInfo("Asia/Seoul")
_COLS = "id, market, symbol, side, qty, amount, scheduled_at, status, detail, created_at, updated_at"


class ManualOrder(BaseModel):
    market: Literal["KR", "US"]
    symbol: str = Field(min_length=1, max_length=20)
    side: Literal["BUY", "SELL"]
    qty: int | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)   # KR=₩, US=$ — 실행 시 현재가로 수량 환산
    scheduled_at: datetime | None = None               # 미지정=즉시

    @model_validator(mode="after")
    def _qty_xor_amount(self):
        if (self.qty is None) == (self.amount is None):
            raise ValueError("qty 또는 amount 중 정확히 하나를 지정")
        return self


def _row_dict(r) -> dict:
    return {
        "id": r[0], "market": r[1], "symbol": r[2], "side": r[3],
        "qty": r[4], "amount": None if r[5] is None else float(r[5]),
        "scheduled_at": r[6].isoformat(), "status": r[7], "detail": r[8],
        "created_at": r[9].isoformat(), "updated_at": r[10].isoformat(),
    }


@router.post("/orders")
def create_order(o: ManualOrder, account_id: str = Depends(current_account_id)):
    at = o.scheduled_at
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=_KST)
    sym = o.symbol.strip().upper() if o.market == "US" else o.symbol.strip()
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO manual_stock_orders (account_id, market, symbol, side, qty, amount, scheduled_at) "
            "VALUES (%s,%s,%s,%s,%s,%s, COALESCE(%s, now())) RETURNING id, status, scheduled_at",
            (account_id, o.market, sym, o.side, o.qty, o.amount, at),
        ).fetchone()
    return {"id": row[0], "status": row[1], "scheduled_at": row[2].isoformat()}


@router.get("/orders")
def list_orders(limit: int = 50, account_id: str = Depends(current_account_id)):
    limit = max(1, min(int(limit), 200))
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM manual_stock_orders WHERE account_id=%s ORDER BY id DESC LIMIT %s",
            (account_id, limit),
        ).fetchall()
    return [_row_dict(r) for r in rows]


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, account_id: str = Depends(current_account_id)):
    """PENDING(미실행)만 취소 가능 — 실행기로 넘어간 주문(PLACED~)은 브로커에 이미 접수됨."""
    with pool.connection() as conn:
        row = conn.execute(
            "UPDATE manual_stock_orders SET status='CANCELED', updated_at=now() "
            "WHERE id=%s AND account_id=%s AND status='PENDING' RETURNING id",
            (order_id, account_id),
        ).fetchone()
    if row is None:
        raise HTTPException(409, "PENDING 상태의 내 주문만 취소할 수 있습니다")
    return {"id": order_id, "status": "CANCELED"}
