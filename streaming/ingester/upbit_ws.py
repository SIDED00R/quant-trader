"""업비트 WebSocket 실시간 체결 수집기 → market.ticks (단일 책임: 수집).

연결이 끊기면 지수 백오프로 재연결하고, produce 실패는 delivery 콜백으로 로깅한다.
배달 워치독(common.kafka_watchdog): 성공 배달이 DELIVERY_STALL_SEC 동안 없으면 SystemExit로
종료 → restart: unless-stopped가 재기동. kafka 행(hang)에도 배달 실패만 삼키며 살아남아
자가복구가 막히는 것 방지(2026-07-18 38시간 정지 사고).
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import websockets

from common.config import KAFKA_BOOTSTRAP_SERVERS, TOPIC_TICKS
from common.constants import HTTP_MAX_BACKOFF
from common.kafka_client import create_producer
from common.kafka_watchdog import DELIVERY_STALL_SEC, DeliveryWatchdog
from common.schemas import Tick
from common.marketdata.symbols import resolve_symbols

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
MAX_BACKOFF = HTTP_MAX_BACKOFF


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
    watchdog = DeliveryWatchdog()

    def _on_delivery(err, msg) -> None:
        watchdog.record_delivery(err)
        if err is not None:
            print(f"[ingester] delivery failed: {err}")

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
                        try:
                            producer.produce(
                                TOPIC_TICKS, key=tick.symbol.encode(),
                                value=tick.to_json(), on_delivery=_on_delivery,
                            )
                            watchdog.record_produce()
                        except BufferError:  # 로컬 큐 만원(브로커 장기 불능 극단) — 한 틱 드랍하고 배수 기회
                            producer.poll(1.0)
                            print("[ingester] local queue full — tick dropped")
                        producer.poll(0)
                        if watchdog.stalled():   # SystemExit은 아래 except에 안 잡히고 그대로 종료
                            print(f"[ingester] 성공 배달 {DELIVERY_STALL_SEC:.0f}s 없음"
                                  f"(pending={watchdog.pending}) — 종료, docker 재시작에 복구 위임")
                            raise SystemExit(1)
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
