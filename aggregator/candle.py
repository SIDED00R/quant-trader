"""1분봉 캔들 집계기 (단일 책임: market.ticks → ClickHouse candles_1m).

종목별 1분 텀블링 윈도우로 OHLCV를 집계해 ClickHouse에 주기적으로 업서트한다.
- 윈도우는 이벤트 타임(trade_ts)을 분 단위로 내림한 값
- 닫힌 윈도우는 워터마크(관측된 최대 윈도우 - 2분) 기준으로 메모리에서 제거
- candles_1m는 ReplacingMergeTree라 같은 (symbol, window_start) 재기록은 멱등 병합
"""
import json
import time
from datetime import datetime, timedelta

from common.clickhouse_client import create_client
from common.config import TOPIC_TICKS
from common.kafka_client import create_consumer

GROUP_ID = "candle-aggregator"
FLUSH_SEC = 2.0
WATERMARK = timedelta(minutes=2)
COLUMNS = ["symbol", "window_start", "open", "high", "low", "close", "volume"]


def floor_minute(ts_iso: str) -> datetime:
    return datetime.fromisoformat(ts_iso).replace(second=0, microsecond=0)


def _rows(candles: dict) -> list:
    return [
        [s, w, c["open"], c["high"], c["low"], c["close"], c["volume"]]
        for (s, w), c in candles.items()
    ]


def run() -> None:
    client = create_client()
    consumer = create_consumer(GROUP_ID)
    consumer.subscribe([TOPIC_TICKS])
    print("[candle] started")

    candles: dict[tuple, dict] = {}
    max_window: datetime | None = None
    last_flush = time.monotonic()
    consumed = False

    try:
        while True:
            msg = consumer.poll(1.0)
            now = time.monotonic()
            if msg is not None and not msg.error():
                consumed = True
                try:
                    t = json.loads(msg.value())
                    sym = t["symbol"]
                    price = float(t["price"])
                    vol = float(t["volume"])
                    wstart = floor_minute(t["trade_ts"])
                except (KeyError, ValueError, TypeError) as e:
                    print(f"[candle] skip bad message: {e}")
                else:
                    if max_window is None or wstart > max_window:
                        max_window = wstart
                    c = candles.get((sym, wstart))
                    if c is None:
                        candles[(sym, wstart)] = {
                            "open": price, "high": price, "low": price,
                            "close": price, "volume": vol,
                        }
                    else:
                        c["high"] = max(c["high"], price)
                        c["low"] = min(c["low"], price)
                        c["close"] = price
                        c["volume"] += vol

            if candles and now - last_flush >= FLUSH_SEC:
                client.insert("candles_1m", _rows(candles), column_names=COLUMNS)
                if consumed:
                    consumer.commit(asynchronous=False)
                    consumed = False
                print(f"[candle] upserted {len(candles)} candles")
                if max_window is not None:
                    cutoff = max_window - WATERMARK
                    for k in [k for k in candles if k[1] < cutoff]:
                        del candles[k]
                last_flush = now
    finally:
        if candles:
            client.insert("candles_1m", _rows(candles), column_names=COLUMNS)
            try:
                consumer.commit(asynchronous=False)
            except Exception:
                pass
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[candle] stopped")
