"""업비트 WebSocket 실시간 체결 수집기 → market.ticks (단일 책임: 수집)."""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import websockets

from common.config import KAFKA_BOOTSTRAP_SERVERS, SYMBOLS, TOPIC_TICKS
from common.kafka_client import create_producer
from common.schemas import Tick

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"


def build_subscribe(symbols: list[str]) -> str:
    return json.dumps(
        [
            {"ticket": str(uuid.uuid4())},
            {"type": "trade", "codes": symbols},
            {"format": "DEFAULT"},
        ]
    )


def to_tick(msg: dict) -> Tick:
    return Tick(
        symbol=msg["code"],
        price=Decimal(msg["trade_price"]),
        volume=Decimal(msg["trade_volume"]),
        side=msg["ask_bid"],
        trade_ts=datetime.fromtimestamp(
            msg["trade_timestamp"] / 1000, tz=timezone.utc
        ).isoformat(),
        seq=int(msg["sequential_id"]),
    )


async def run() -> None:
    producer = create_producer()
    print(f"[ingester] connecting Upbit | symbols={SYMBOLS} | kafka={KAFKA_BOOTSTRAP_SERVERS}")
    try:
        async with websockets.connect(UPBIT_WS_URL, ping_interval=60) as ws:
            await ws.send(build_subscribe(SYMBOLS))
            count = 0
            async for raw in ws:
                msg = json.loads(raw, parse_float=Decimal)
                if msg.get("type") != "trade":
                    continue
                tick = to_tick(msg)
                producer.produce(TOPIC_TICKS, key=tick.symbol.encode(), value=tick.to_json())
                producer.poll(0)
                count += 1
                if count % 50 == 0:
                    print(f"[ingester] produced {count} ticks (last: {tick.symbol} @ {tick.price})")
    finally:
        producer.flush(5)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("[ingester] stopped")
