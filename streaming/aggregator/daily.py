"""일봉 집계기 (단일 책임: candles_1m → candles_1d 리샘플, 완료된 날 주기 업서트).

라이브 aggregator가 만든 분봉(candles_1m)을 일 단위로 리샘플해 candles_1d를 최신으로 유지한다 —
REST 백필이 채운 과거 + 라이브에서 채워지는 최근 날. 일봉 전략·대시보드 스탠스가 매일 최신을 보게 한다.
오늘(미마감)은 제외(window_start < 오늘 00:00 UTC). ReplacingMergeTree(updated_at)라 재실행 멱등.
일봉이라 저빈도(기본 1시간)로 충분하다.
"""
import time

from common.clickhouse_client import create_client

SLEEP_SEC = 3600
LOOKBACK_DAYS = 7   # 최근 완료일만 리샘플(효율) — 과거분은 backfill_daily(직접 일봉 REST)가 채움

_SQL = f"""INSERT INTO candles_1d (symbol, window_start, open, high, low, close, volume, updated_at)
SELECT symbol, toStartOfDay(window_start) AS d,
       argMin(open, window_start), max(high), min(low), argMax(close, window_start), sum(volume), now64(3)
FROM candles_1m FINAL
WHERE window_start >= toStartOfDay(now()) - INTERVAL {LOOKBACK_DAYS} DAY
  AND window_start < toStartOfDay(now())
GROUP BY symbol, d"""


def run() -> None:
    client = create_client()
    print(f"[daily-agg] started — candles_1m → candles_1d (최근 {LOOKBACK_DAYS}일, {SLEEP_SEC}s 주기)")
    while True:
        try:
            client.command(_SQL)
            print("[daily-agg] candles_1d 리샘플 업서트 완료")
        except Exception as e:   # CH 일시 오류 → 다음 주기 재시도(데몬 유지)
            print(f"[daily-agg] error: {e}")
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[daily-agg] stopped")
