"""토스 일봉 → ClickHouse 적재 (단일 책임: CH 어댑터).

fetch는 `common/marketdata/toss_daily.py`(app 이미지 공용 — 텔레그램 차트 봇이 온디맨드 사용),
CH 적재(`upsert_clickhouse`)는 배치 책임이라 여기. stock_candles_1d는 ReplacingMergeTree라 재실행 멱등.
"""
from batch.candles._upsert import upsert
from common.constants import COLUMNS_STOCK_CANDLES_1D

_COLUMNS = COLUMNS_STOCK_CANDLES_1D


def upsert_clickhouse(client, rows: list, table: str = "stock_candles_1d") -> int:
    """rows를 ClickHouse table에 insert(_upsert 공용 코어 — 멱등·적재 행수 반환)."""
    return upsert(client, rows, table, _COLUMNS)
