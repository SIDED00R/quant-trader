"""일봉 집계기 (단일 책임: candles_1m → candles_1d 리샘플, 완료된 날 주기 업서트).

라이브 aggregator가 만든 분봉(candles_1m)을 일 단위로 리샘플해 candles_1d를 최신으로 유지한다 —
REST 백필이 채운 과거 + 라이브에서 채워지는 최근 날. 일봉 전략·대시보드 스탠스가 매일 최신을 보게 한다.
오늘(미마감)은 제외(window_start < 오늘 00:00 UTC). ReplacingMergeTree(updated_at)라 재실행 멱등.
일봉이라 저빈도(기본 1시간)로 충분하다.
"""
import logging
import time

from common import log
from common.clickhouse_client import create_client

logger = logging.getLogger(__name__)

SLEEP_SEC = 3600
LOOKBACK_DAYS = 2   # 최근 완료일만 리샘플 — 마감일은 불변이라 어제+그제로 충분(부팅 갭 여유). 과거분은 backfill_daily가 채움

_SQL = f"""INSERT INTO candles_1d (symbol, window_start, open, high, low, close, volume, updated_at)
SELECT symbol, toStartOfDay(window_start) AS d,
       argMin(open, window_start), max(high), min(low), argMax(close, window_start), sum(volume), now64(3)
FROM candles_1m FINAL
WHERE window_start >= toStartOfDay(now()) - INTERVAL {LOOKBACK_DAYS} DAY
  AND window_start < toStartOfDay(now())
GROUP BY symbol, d"""


def run() -> None:
    client = create_client()
    logger.info(f"started — candles_1m → candles_1d (최근 {LOOKBACK_DAYS}일, {SLEEP_SEC}s 주기)")
    while True:
        try:
            client.command(_SQL)
            logger.info("candles_1d 리샘플 업서트 완료")
        except Exception as e:   # CH 일시 오류 → 다음 주기 재시도(데몬 유지)
            logger.error(f"error: {e}")
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    log.setup()
    try:
        run()
    except KeyboardInterrupt:
        logger.info("stopped")
