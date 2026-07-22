"""체결 엔진 (단일 책임: 주문 매칭 → executions).

market.ticks + orders 를 동시 소비한다.
- 종목별 최신가(latest_price)를 틱으로 갱신
- 시장가(MARKET): 최신가로 즉시 체결, 가격 미확인 종목은 pending에 보관 후 첫 틱에 체결
- 지정가(LIMIT): 가격 조건 충족 시 지정가로 체결, 미충족이면 limit_orders에 보관 후 매 틱에 재확인
  · BUY  LIMIT: 시장가 <= 지정가 → 체결
  · SELL LIMIT: 시장가 >= 지정가 → 체결

견고성:
- execution_id는 order_id 기반 결정적 값(uuid5) → 재소비 시에도 portfolio가 멱등 처리
- 기동 시 DB의 PENDING 주문(MARKET/LIMIT)을 재적재 → 재시작에도 주문 유실 없음
- enable.auto.commit=False, executions를 flush로 확정 전송 후 오프셋 커밋
- latest_price/pending/limit_orders가 인메모리이므로 이 엔진은 단일 인스턴스로만 실행한다.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from common import log
from common.config import FEE_RATE, TOPIC_EXECUTIONS, TOPIC_ORDERS, TOPIC_TICKS
from common.kafka_client import create_consumer, create_producer
from common.postgres_client import close_pool, open_pool, pool
from common.schemas import Execution

logger = logging.getLogger(__name__)

GROUP_ID = "matching-engine"
COMMIT_SEC = 1.0
EXEC_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "coin-auto-trader.executions")


def load_pending_orders() -> tuple[dict, dict]:
    """기동 시 DB의 미체결(PENDING) 주문을 시장가/지정가로 나눠 재적재한다."""
    pending: dict[str, list[dict]] = {}
    limit_orders: dict[str, list[dict]] = {}
    open_pool()
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT order_id, account_id, symbol, side, type, price, quantity "
                "FROM orders WHERE status='PENDING'"
            ).fetchall()
    finally:
        close_pool()
    for r in rows:
        order = {
            "order_id": str(r[0]), "account_id": r[1], "symbol": r[2],
            "side": r[3], "type": r[4], "price": r[5], "quantity": r[6],
        }
        bucket = pending if r[4] == "MARKET" else limit_orders
        bucket.setdefault(r[2], []).append(order)
    return pending, limit_orders


def limit_fill_price(order: dict, market_price: Decimal):
    """지정가 조건 충족 시 체결가(=시장가, 지정가보다 유리)를 반환, 아니면 None.

    지정가는 트리거 임계값일 뿐이며, 실제 체결은 트리거 시점의 시장가로 한다
    (BUY는 지정가 이하, SELL은 지정가 이상이라 항상 트레이더에게 유리하거나 같다).
    """
    limit = Decimal(str(order["price"]))
    if order["side"] == "BUY" and market_price <= limit:
        return market_price
    if order["side"] == "SELL" and market_price >= limit:
        return market_price
    return None


def execute(order: dict, price: Decimal, producer) -> None:
    qty = Decimal(str(order["quantity"]))
    fee = (price * qty * FEE_RATE).quantize(Decimal("0.0001"))
    ex = Execution(
        execution_id=str(uuid.uuid5(EXEC_NAMESPACE, order["order_id"])),
        order_id=order["order_id"],
        account_id=order["account_id"],
        symbol=order["symbol"],
        side=order["side"],
        price=price,
        quantity=qty,
        fee=fee,
        ts=datetime.now(timezone.utc).isoformat(),
    )
    producer.produce(TOPIC_EXECUTIONS, key=order["symbol"].encode(), value=ex.to_json())
    logger.info(f"filled {order['side']} {order['type']} {order['symbol']} qty={qty} @ {price} (fee={fee})")


def run() -> None:
    producer = create_producer()
    consumer = create_consumer(GROUP_ID, enable_auto_commit=False)
    consumer.subscribe([TOPIC_TICKS, TOPIC_ORDERS])
    latest_price: dict[str, Decimal] = {}
    pending, limit_orders = load_pending_orders()
    logger.info(f"started (reseeded {sum(len(v) for v in pending.values())} market, "
          f"{sum(len(v) for v in limit_orders.values())} limit)")
    last_commit = time.monotonic()
    consumed = False

    try:
        while True:
            msg = consumer.poll(1.0)
            now = time.monotonic()
            if msg is not None and not msg.error():
                consumed = True
                data = json.loads(msg.value())
                if msg.topic() == TOPIC_TICKS:
                    symbol = data["symbol"]
                    price = Decimal(str(data["price"]))
                    latest_price[symbol] = price
                    for order in pending.pop(symbol, []):
                        execute(order, price, producer)
                    if symbol in limit_orders:
                        still_waiting = []
                        for order in limit_orders[symbol]:
                            fill = limit_fill_price(order, price)
                            if fill is not None:
                                execute(order, fill, producer)
                            else:
                                still_waiting.append(order)
                        limit_orders[symbol] = still_waiting
                elif msg.topic() == TOPIC_ORDERS:
                    symbol = data["symbol"]
                    if data.get("type") == "MARKET":
                        if symbol in latest_price:
                            execute(data, latest_price[symbol], producer)
                        else:
                            pending.setdefault(symbol, []).append(data)
                    elif data.get("type") == "LIMIT":
                        fill = (limit_fill_price(data, latest_price[symbol])
                                if symbol in latest_price else None)
                        if fill is not None:
                            execute(data, fill, producer)
                        else:
                            limit_orders.setdefault(symbol, []).append(data)

            if consumed and now - last_commit >= COMMIT_SEC:
                producer.flush()
                consumer.commit(asynchronous=False)
                last_commit = now
                consumed = False
    finally:
        producer.flush(5)
        try:
            consumer.commit(asynchronous=False)
        except Exception:
            pass
        consumer.close()


if __name__ == "__main__":
    log.setup()
    try:
        run()
    except KeyboardInterrupt:
        logger.info("stopped")
