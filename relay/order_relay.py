"""주문 아웃박스 릴레이 (단일 책임: order_outbox → orders 토픽 발행).

order_outbox의 미발행 레코드를 orders 토픽으로 발행한 뒤 published 처리한다.
주문 INSERT와 outbox 기록이 한 트랜잭션이므로 발행은 at-least-once로 보장되고,
중복 발행은 결정적 execution_id로 하류(체결엔진→포트폴리오)에서 멱등 처리된다.
FOR UPDATE SKIP LOCKED로 다중 인스턴스에서도 안전하다.
"""
import time

from common.config import TOPIC_ORDERS
from common.kafka_client import create_producer
from common.postgres_client import close_pool, open_pool, pool

BATCH = 100
POLL_SEC = 0.2


def run() -> None:
    open_pool()
    producer = create_producer()
    print("[relay] started")
    try:
        while True:
            with pool.connection() as conn:
                rows = conn.execute(
                    "SELECT id, symbol, payload FROM order_outbox "
                    "WHERE NOT published ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
                    (BATCH,),
                ).fetchall()
                if not rows:
                    time.sleep(POLL_SEC)
                    continue
                for _id, symbol, payload in rows:
                    producer.produce(TOPIC_ORDERS, key=symbol.encode(), value=payload.encode())
                producer.flush()
                conn.execute(
                    "UPDATE order_outbox SET published=TRUE WHERE id = ANY(%s)",
                    ([r[0] for r in rows],),
                )
                print(f"[relay] published {len(rows)} orders")
    finally:
        producer.flush(5)
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[relay] stopped")
