"""CSV 분봉 캐시 → ClickHouse candles_1m 적재 (단일 책임: 기존 백필 CSV를 DB로 통일).

업비트 REST로 받아 data/candles/<unit>m/*.csv에 둔 과거 분봉을 ClickHouse candles_1m으로 옮긴다.
ReplacingMergeTree라 재실행 멱등. 대용량(종목당 ~100만행)이라 배치 단위로 스트리밍 insert한다.
사전조건: docker compose up -d clickhouse + python -m scripts.init_db.
예) .venv/Scripts/python -m batch.backtest.csv_to_clickhouse --symbols KRW-BTC,KRW-ETH --unit 1
"""
import argparse
import csv
import sys
from datetime import datetime, timezone

from common.clickhouse_client import create_client
from common.config import SYMBOLS
from common.constants import COLUMNS_CANDLES
from batch.backtest.upbit_candles import cache_path

_COLUMNS = COLUMNS_CANDLES
_BATCH = 100_000


def _rows(path: str, market: str):
    """CSV 행 → [symbol, window_start(UTC datetime), o,h,l,c,v] 제너레이터."""
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ws = datetime.fromtimestamp(int(r["ts_ms"]) / 1000.0, timezone.utc)
            yield [market, ws, float(r["open"]), float(r["high"]), float(r["low"]),
                   float(r["close"]), float(r["volume"])]


def load_csv_to_clickhouse(market: str, unit: int, cache_dir: str, client, log=print) -> int:
    """market의 CSV 캐시를 candles_1m에 배치 insert. 적재 행수 반환(파일 없으면 0)."""
    import os
    path = cache_path(cache_dir, market, unit)
    if not os.path.exists(path):
        log(f"[csv2ch] {market}: 캐시 없음(스킵)")
        return 0
    total = 0
    batch: list = []
    for row in _rows(path, market):
        batch.append(row)
        if len(batch) >= _BATCH:
            client.insert("candles_1m", batch, column_names=_COLUMNS)
            total += len(batch)
            batch = []
            log(f"[csv2ch] {market}: {total:,}행 적재 중…")
    if batch:
        client.insert("candles_1m", batch, column_names=_COLUMNS)
        total += len(batch)
    log(f"[csv2ch] {market}: 완료 {total:,}행")
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="CSV 분봉 캐시 → ClickHouse candles_1m")
    p.add_argument("--symbols", default=",".join(SYMBOLS), help="쉼표 구분")
    p.add_argument("--unit", type=int, default=1, help="캐시 분봉 단위")
    p.add_argument("--cache-dir", default="data/candles", help="CSV 캐시 디렉터리")
    a = p.parse_args(argv)
    markets = [s.strip() for s in a.symbols.split(",") if s.strip()]
    if not markets:
        print("[csv2ch] --symbols 가 비었습니다.", file=sys.stderr)
        return 2
    try:
        client = create_client()
        total = sum(load_csv_to_clickhouse(m, a.unit, a.cache_dir, client) for m in markets)
    except Exception as e:
        print(f"[csv2ch] 실패: {e} (ClickHouse 기동·init_db 확인)", file=sys.stderr)
        return 2
    print(f"[csv2ch] 완료: {markets} → candles_1m ({total:,}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
