"""주문 기록 (단일 책임: orders + order_outbox 원자적 INSERT).

주문 API 라우트와 자동매매 봇이 공유하는 주문 생성의 단일 경로.
Kafka 발행은 relay 가 outbox 를 읽어 수행한다(DB-Kafka dual-write 갭 제거).
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from common.postgres_client import pool
from common.schemas import Order


def place_order(
    account_id: str,
    symbol: str,
    side: str,
    type_: str,
    quantity: Decimal,
    price: Decimal | None = None,
) -> str:
    """주문을 PENDING 으로 기록하고 order_id 를 반환한다."""
    order_id = uuid.uuid4()
    order = Order(
        order_id=str(order_id),
        account_id=account_id,
        symbol=symbol,
        side=side,
        type=type_,
        price=price,
        quantity=quantity,
        ts=datetime.now(timezone.utc).isoformat(),
    )
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO orders (order_id, account_id, symbol, side, type, price, quantity, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING')",
            (order_id, account_id, symbol, side, type_, price, quantity),
        )
        conn.execute(
            "INSERT INTO order_outbox (order_id, symbol, payload) VALUES (%s,%s,%s)",
            (order_id, symbol, order.to_json().decode()),
        )
    return str(order_id)
