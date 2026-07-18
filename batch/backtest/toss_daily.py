"""토스 일봉 → ClickHouse 적재 (단일 책임: CH 어댑터).

fetch는 `common/marketdata/toss_daily.py`로 이동(app 이미지 공용 — 텔레그램 차트 봇이 온디맨드 사용). 여기서 re-export해
기존 사용처(`refresh_stock_daily`·`backfill_stock_daily`·`selective_stock_backfill`의 `from batch.backtest.toss_daily import fetch_daily`)는 무변경으로 동작한다.
CH 적재(`upsert_clickhouse`)는 배치 책임이라 잔류. stock_candles_1d는 ReplacingMergeTree라 재실행 멱등.
"""
from common.constants import COLUMNS_STOCK_CANDLES_1D
from common.marketdata.toss_daily import fetch_daily  # noqa: F401 — re-export(기존 import 경로 보존)

_COLUMNS = COLUMNS_STOCK_CANDLES_1D


def upsert_clickhouse(client, rows: list, table: str = "stock_candles_1d") -> int:
    """rows를 ClickHouse table에 insert. 적재 행수 반환."""
    if not rows:
        return 0
    client.insert(table, rows, column_names=_COLUMNS)
    return len(rows)
