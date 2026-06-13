"""체결 엔진 (단일 책임: 시장가 주문 매칭 → executions).

market.ticks + orders 를 동시 소비한다.
- 종목별 최신가(latest_price)를 틱으로 갱신
- 시장가 주문은 최신가로 즉시 체결, 가격 미확인 종목은 pending에 보관 후 첫 틱에 체결
- 지정가(LIMIT)는 기능 #7에서 처리

견고성:
- execution_id는 order_id 기반 결정적 값(uuid5) → 재소비 시에도 portfolio가 멱등 처리
- 기동 시 DB의 PENDING 시장가 주문을 pending으로 재적재 → 재시작에도 주문 유실 없음
- enable.auto.commit=False, executions를 flush로 브로커 확정 후 오프셋 커밋
- latest_price/pending이 인메모리이므로 이 엔진은 단일 인스턴스로만 실행한다.
"""
import json
import time
import uuid
from datetime import datetime, timezone

from common.config import FEE_RATE, TOPIC_EXECUTIONS, TOPIC_ORDERS, TOPIC_TICKS
from common.kafka_client import create_consumer, create_producer
from common.postgres_client import close_pool, open_pool, pool
from common.schemas import Execution

GROUP_ID = "matching-engine"
COMMIT_SEC = 1.0
# order_id로부터 결정적 execution_id를 만들기 위한 네임스페이스
EXEC_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "coin-auto-trader.executions")


def load_pending_orders() -> dict[str, list[dict]]:
    """기동 시 DB의 미체결(PENDING) 시장가 주문을 재적재한다."""
    pending: dict[str, list[dict]] = {}
    open_pool()
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT order_id, account_id, symbol, side, type, quantity "
                "FROM orders WHERE status='PENDING' AND type='MARKET'"
            ).fetchall()
    finally:
        close_pool()
    for r in rows:
        order = {
            "order_id": str(r[0]), "account_id": r[1], "symbol": r[2],
            "side": r[3], "type": r[4], "quantity": float(r[5]),
        }
        pending.setdefault(r[2], []).append(order)
    return pending


def execute(order: dict, price: float, producer) -> None:
    qty = float(order["quantity"])
    fee = round(price * qty * FEE_RATE, 4)
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
    print(f"[engine] filled {order['side']} {order['symbol']} qty={qty} @ {price} (fee={fee})")


def run() -> None:
    producer = create_producer()
    consumer = create_consumer(GROUP_ID, enable_auto_commit=False)
    consumer.subscribe([TOPIC_TICKS, TOPIC_ORDERS])
    latest_price: dict[str, float] = {}
    pending = load_pending_orders()
    print(f"[engine] started (reseeded {sum(len(v) for v in pending.values())} pending orders)")
    last_commit = time.monotonic()
    consumed = False  # 마지막 커밋 이후 소비한 메시지가 있는가

    try:
        while True:
            msg = consumer.poll(1.0)
            now = time.monotonic()
            if msg is not None and not msg.error():
                consumed = True
                data = json.loads(msg.value())
                if msg.topic() == TOPIC_TICKS:
                    symbol = data["symbol"]
                    latest_price[symbol] = float(data["price"])
                    for order in pending.pop(symbol, []):
                        execute(order, latest_price[symbol], producer)
                elif msg.topic() == TOPIC_ORDERS:
                    if data.get("type") == "MARKET":
                        symbol = data["symbol"]
                        if symbol in latest_price:
                            execute(data, latest_price[symbol], producer)
                        else:
                            pending.setdefault(symbol, []).append(data)
                    # 지정가(LIMIT)는 기능 #7에서 처리

            # executions를 브로커에 확정 전송한 뒤에만 오프셋 커밋
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
    try:
        run()
    except KeyboardInterrupt:
        print("[engine] stopped")
