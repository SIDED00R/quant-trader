"""Kafka producer/consumer 팩토리 (단일 책임: Kafka 연결)."""
from confluent_kafka import Consumer, Producer

from common.config import KAFKA_BOOTSTRAP_SERVERS


def create_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "enable.idempotence": True,
            "linger.ms": 50,
            "compression.type": "lz4",
        }
    )


def create_consumer(
    group_id: str,
    enable_auto_commit: bool = False,
    auto_offset_reset: str = "earliest",
) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "auto.offset.reset": auto_offset_reset,
            "enable.auto.commit": enable_auto_commit,
        }
    )
