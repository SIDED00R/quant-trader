"""체결 엔진 (단일 책임: 시장가 주문 매칭 → executions).

market.ticks + orders 를 동시 소비한다.
- 종목별 최신가(latest_price)를 틱으로 갱신
- 시장가 주문은 최신가로 즉시 체결, 가격 미확인 종목은 pending에 보관 후 첫 틱에 체결
- 지정가(LIMIT)는 기능 #7에서 처리

주의: latest_price/pending이 인메모리이므로 이 엔진은 단일 인스턴스로만 실행한다.
"""
import json
import uuid
from datetime import datetime, timezone

from common.config import FEE_RATE, TOPIC_EXECUTIONS, TOPIC_ORDERS, TOPIC_TICKS
from common.kafka_client import create_consumer, create_producer
from common.schemas import Execution

GROUP_ID = "matching-engine"


def execute(order: dict, price: float, producer) -> None:
    qty = float(order["quantity"])
    fee = round(price * qty * FEE_RATE, 4)
    ex = Execution(
        execution_id=str(uuid.uuid4()),
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
    consumer = create_consumer(GROUP_ID, enable_auto_commit=True)
    consumer.subscribe([TOPIC_TICKS, TOPIC_ORDERS])
    latest_price: dict[str, float] = {}
    pending: dict[str, list[dict]] = {}
    print("[engine] matching engine started")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            data = json.loads(msg.value())

            if msg.topic() == TOPIC_TICKS:
                symbol = data["symbol"]
                latest_price[symbol] = float(data["price"])
                for order in pending.pop(symbol, []):
                    execute(order, latest_price[symbol], producer)
            elif msg.topic() == TOPIC_ORDERS:
                if data.get("type") != "MARKET":
                    continue  # 지정가는 기능 #7에서 처리
                symbol = data["symbol"]
                if symbol in latest_price:
                    execute(data, latest_price[symbol], producer)
                else:
                    pending.setdefault(symbol, []).append(data)

            producer.poll(0)
    finally:
        producer.flush(5)
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[engine] stopped")
