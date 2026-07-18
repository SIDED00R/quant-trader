"""stock.ticks → ClickHouse 적재 (단일 책임: 주식 틱 싱크).

코인 sink/tick_clickhouse.py 패턴 미러(별도 GROUP_ID·테이블). Tick 스키마를 공유한다.
"""
import time

from common.clickhouse_client import create_client
from common.config import TOPIC_STOCK_TICKS
from common.constants import COLUMNS_TICKS
from common.kafka_client import create_consumer
from streaming.sink._parse import parse_row

GROUP_ID = "stock-tick-clickhouse-sink"
TABLE = "stock_ticks"
BATCH_SIZE = 500
FLUSH_SEC = 2.0
COLUMNS = COLUMNS_TICKS


def run() -> None:
    client = create_client()
    consumer = create_consumer(GROUP_ID)
    consumer.subscribe([TOPIC_STOCK_TICKS])
    print(f"[stock-sink] consuming {TOPIC_STOCK_TICKS} → ClickHouse {TABLE}")

    batch: list = []
    last_flush = time.monotonic()
    total = 0
    try:
        while True:
            msg = consumer.poll(1.0)
            now = time.monotonic()
            if msg is not None and not msg.error():
                try:
                    batch.append(parse_row(msg.value()))
                except (KeyError, ValueError, TypeError) as e:
                    print(f"[stock-sink] skip bad message: {e}")
            if batch and (len(batch) >= BATCH_SIZE or now - last_flush >= FLUSH_SEC):
                client.insert(TABLE, batch, column_names=COLUMNS)
                consumer.commit(asynchronous=False)
                total += len(batch)
                print(f"[stock-sink] inserted {len(batch)} rows (total {total})")
                batch.clear()
                last_flush = now
    finally:
        if batch:
            client.insert(TABLE, batch, column_names=COLUMNS)
            consumer.commit(asynchronous=False)
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[stock-sink] stopped")
