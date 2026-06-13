"""주문 생성 라우트 (단일 책임: 주문 접수)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common.config import TOPIC_ORDERS
from common.postgres_client import pool
from common.schemas import Order

router = APIRouter()

SIDES = {"BUY", "SELL"}
TYPES = {"MARKET", "LIMIT"}


class OrderRequest(BaseModel):
    account_id: str = "demo"
    symbol: str
    side: str
    type: str = "MARKET"
    quantity: float
    price: float | None = None


@router.post("/orders")
def create_order(req: OrderRequest, request: Request):
    if req.side not in SIDES:
        raise HTTPException(400, f"side must be one of {SIDES}")
    if req.type not in TYPES:
        raise HTTPException(400, f"type must be one of {TYPES}")
    if req.quantity <= 0:
        raise HTTPException(400, "quantity must be > 0")
    if req.type == "LIMIT" and req.price is None:
        raise HTTPException(400, "LIMIT order requires price")

    order_id = uuid.uuid4()
    ts = datetime.now(timezone.utc).isoformat()
    quantity = Decimal(str(req.quantity))
    price = Decimal(str(req.price)) if req.price is not None else None

    with pool.connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM accounts WHERE account_id=%s", (req.account_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "account not found")
        conn.execute(
            "INSERT INTO orders (order_id, account_id, symbol, side, type, price, quantity, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING')",
            (order_id, req.account_id, req.symbol, req.side, req.type, price, quantity),
        )

    order = Order(
        order_id=str(order_id),
        account_id=req.account_id,
        symbol=req.symbol,
        side=req.side,
        type=req.type,
        price=price,
        quantity=quantity,
        ts=ts,
    )
    producer = request.app.state.producer
    producer.produce(TOPIC_ORDERS, key=req.symbol.encode(), value=order.to_json())
    producer.poll(0)

    return {"order_id": str(order_id), "status": "PENDING"}
