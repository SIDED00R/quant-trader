"""ClickHouse 캔들 데이터 소스 (단일 책임: candles_1m/1d replay).

candles 테이블(symbol, window_start, ..., close)을 전역 시간순(window_start, symbol)으로 스트리밍해
BTick(종가)을 yield한다. 봉 종가를 가격 시계열로 쓴다(라이브 aggregator가 적재하는 1분봉과 동일 소스).
table=candles_1d면 장기 일봉(backfill_daily 적재본) — 저회전 추세 전략의 장기 백테스트용.
업비트 REST 직접 수집/캐시는 backtest.upbit_candles 가 담당한다(소스는 run.py --source로 선택).
ReplacingMergeTree 중복은 FINAL로 정리.
"""
from decimal import Decimal

import clickhouse_connect

from common.config import (
    CLICKHOUSE_DB,
    CLICKHOUSE_HOST,
    CLICKHOUSE_HTTP_PORT,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_USER,
)
from batch.backtest.models import BTick


def _client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_HTTP_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DB,
    )


_TABLES = {"candles_1m", "candles_1d"}  # 허용 테이블(SQL 식별자는 파라미터화 불가 → 화이트리스트로 주입 차단)


def load_clickhouse_candles(symbols=None, start=None, end=None, table="candles_1m"):
    """candles 테이블을 전역 시간순으로 스트리밍 yield(종가 기준).

    symbols: 종목 리스트(None이면 전체). start/end: 'YYYY-MM-DD HH:MM:SS' 등 문자열(UTC, None이면 무제한).
    table: 'candles_1m'(기본) 또는 'candles_1d'(장기 일봉). ReplacingMergeTree 중복 정리를 위해 항상 FINAL.
    """
    if table not in _TABLES:
        raise ValueError(f"unknown table '{table}'. allowed: {sorted(_TABLES)}")
    client = _client()
    conds = ["1=1"]
    params: dict = {}
    if symbols:
        conds.append("symbol IN {symbols:Array(String)}")
        params["symbols"] = list(symbols)
    if start:
        conds.append("window_start >= parseDateTimeBestEffort({start:String}, 'UTC')")
        params["start"] = start
    if end:
        conds.append("window_start < parseDateTimeBestEffort({end:String}, 'UTC')")
        params["end"] = end
    where = " AND ".join(conds)
    query = (
        "SELECT symbol, close, toUnixTimestamp64Milli(toDateTime64(window_start, 3)) AS ts_ms "
        f"FROM {table} FINAL WHERE {where} ORDER BY window_start, symbol"
    )
    with client.query_row_block_stream(query, parameters=params) as stream:
        for block in stream:
            for row in block:
                yield BTick(symbol=row[0], price=Decimal(str(row[1])), ts=row[2] / 1000.0)
