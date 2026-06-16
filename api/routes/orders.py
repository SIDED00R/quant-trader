"""주문 생성 라우트 (단일 책임: 주문 요청 검증).

계정은 로그인 세션에서 결정한다(요청으로 타인 계정 주문 불가).
실제 기록(orders + outbox)은 common.order_writer.place_order 가 담당.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.security import current_account_id
from common.order_writer import place_order

router = APIRouter()

SIDES = {"BUY", "SELL"}
TYPES = {"MARKET", "LIMIT"}


class OrderRequest(BaseModel):
    symbol: str
    side: str
    type: str = "MARKET"
    quantity: float
    price: float | None = None


@router.post("/orders")
def create_order(req: OrderRequest, account_id: str = Depends(current_account_id)):
    if req.side not in SIDES:
        raise HTTPException(400, f"side must be one of {SIDES}")
    if req.type not in TYPES:
        raise HTTPException(400, f"type must be one of {TYPES}")
    if req.quantity <= 0:
        raise HTTPException(400, "quantity must be > 0")
    if req.type == "LIMIT" and req.price is None:
        raise HTTPException(400, "LIMIT order requires price")

    order_id = place_order(
        account_id=account_id,
        symbol=req.symbol,
        side=req.side,
        type_=req.type,
        quantity=Decimal(str(req.quantity)),
        price=Decimal(str(req.price)) if req.price is not None else None,
    )
    return {"order_id": order_id, "status": "PENDING"}
