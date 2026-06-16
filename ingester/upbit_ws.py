"""업비트 WebSocket 실시간 체결 수집기 → market.ticks (단일 책임: 수집).

연결이 끊기면 지수 백오프로 재연결하고, produce 실패는 delivery 콜백으로 로깅한다.
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import websockets

from common.config import KAFKA_BOOTSTRAP_SERVERS, TOPIC_TICKS
from common.kafka_client import create_producer
from common.schemas import Tick
from common.symbols import resolve_symbols

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
MAX_BACKOFF = 30


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


def _on_delivery(err, msg) -> None:
    if err is not None:
        print(f"[ingester] delivery failed: {err}")


async def run() -> None:
    producer = create_producer()
    print(f"[ingester] connecting Upbit | kafka={KAFKA_BOOTSTRAP_SERVERS}")
    backoff = 1
    count = 0
    try:
        while True:
            try:
                async with websockets.connect(UPBIT_WS_URL, ping_interval=60) as ws:
                    backoff = 1
                    symbols = resolve_symbols()  # (재)연결마다 재해석 → 콜드스타트 실패 후 복구 반영
                    print(f"[ingester] subscribing {len(symbols)} symbols")
                    await ws.send(build_subscribe(symbols))
                    async for raw in ws:
                        msg = json.loads(raw, parse_float=Decimal)
                        if msg.get("type") != "trade":
                            continue
                        tick = to_tick(msg)
                        producer.produce(
                            TOPIC_TICKS, key=tick.symbol.encode(),
                            value=tick.to_json(), on_delivery=_on_delivery,
                        )
                        producer.poll(0)
                        count += 1
                        if count % 50 == 0:
                            print(f"[ingester] produced {count} ticks (last: {tick.symbol} @ {tick.price})")
            except (websockets.WebSocketException, OSError) as e:
                print(f"[ingester] connection lost: {e}; reconnect in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
    finally:
        producer.flush(5)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("[ingester] stopped")
