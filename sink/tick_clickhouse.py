"""market.ticks → ClickHouse 적재 (단일 책임: 틱 싱크)."""
import json
import time
from datetime import datetime

from confluent_kafka import Consumer

from common.clickhouse_client import create_client, ensure_schema
from common.config import KAFKA_BOOTSTRAP_SERVERS, TOPIC_TICKS

GROUP_ID = "tick-clickhouse-sink"
BATCH_SIZE = 500
FLUSH_SEC = 2.0
COLUMNS = ["symbol", "price", "volume", "side", "trade_ts"]


def create_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )


def parse_row(value: bytes) -> list:
    t = json.loads(value)
    return [
        t["symbol"],
        float(t["price"]),
        float(t["volume"]),
        t["side"],
        datetime.fromisoformat(t["trade_ts"]),
    ]


def run() -> None:
    client = create_client()
    ensure_schema(client)
    consumer = create_consumer()
    consumer.subscribe([TOPIC_TICKS])
    print(f"[sink] consuming {TOPIC_TICKS} → ClickHouse ticks")

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
                    print(f"[sink] skip bad message: {e}")
            if batch and (len(batch) >= BATCH_SIZE or now - last_flush >= FLUSH_SEC):
                client.insert("ticks", batch, column_names=COLUMNS)
                consumer.commit(asynchronous=False)
                total += len(batch)
                print(f"[sink] inserted {len(batch)} rows (total {total})")
                batch.clear()
                last_flush = now
    finally:
        if batch:
            client.insert("ticks", batch, column_names=COLUMNS)
            consumer.commit(asynchronous=False)
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[sink] stopped")
