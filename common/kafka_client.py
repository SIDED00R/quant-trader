"""Kafka producer 팩토리 (단일 책임: Kafka 연결)."""
from confluent_kafka import Producer

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
