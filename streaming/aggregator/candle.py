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
from common.constants import COLUMNS_CANDLES
from common.kafka_client import create_consumer

GROUP_ID = "candle-aggregator"
FLUSH_SEC = 2.0
WATERMARK = timedelta(minutes=2)
COLUMNS = COLUMNS_CANDLES


def floor_minute(ts_iso: str) -> datetime:
    """tz-aware(UTC) ISO 타임스탬프를 분 단위로 내림한 윈도우 키(tz-aware 유지)."""
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
    dirty: set[tuple] = set()          # 직전 플러시 이후 갱신된 (symbol, window) — 이것만 재적재
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
                    dirty.add((sym, wstart))     # 이 봉을 다음 플러시 재적재 대상으로

            if now - last_flush >= FLUSH_SEC:
                if dirty:                        # 갱신분만 재적재(마감봉·무입력 구간 재적재 제거)
                    client.insert("candles_1m", _rows({k: candles[k] for k in dirty}), column_names=COLUMNS)
                    print(f"[candle] upserted {len(dirty)} candles")
                    dirty.clear()
                if consumed:                     # insert와 분리 — 갱신 없어도(bad message 등) 오프셋은 진행
                    consumer.commit(asynchronous=False)
                    consumed = False
                if max_window is not None:
                    cutoff = max_window - WATERMARK
                    for k in [k for k in candles if k[1] < cutoff]:
                        del candles[k]
                last_flush = now
    finally:
        if dirty:                                # 미플러시 갱신분만(나머지는 이미 적재됨)
            client.insert("candles_1m", _rows({k: candles[k] for k in dirty}), column_names=COLUMNS)
        try:
            consumer.commit(asynchronous=False)  # 마지막 오프셋 커밋(갱신 유무 무관)
        except Exception:
            pass
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[candle] stopped")
