"""틱 데이터 소스 (단일 책임: ClickHouse 틱 replay).

ticks를 시간순으로 스트리밍해 BTick을 yield한다. 정렬은 ORDER BY trade_ts, symbol, seq:
- 종목 내부: trade_ts→seq 순(거래소 sequential_id 단조와 정합) → SMA 윈도우가 라이브와 동일.
- 종목 간: 같은 ms(trade_ts는 ms 해상도)면 symbol 사전순으로 결정적 고정 → run마다 재현 가능.
  단 seq는 종목별 카운터라 종목 간 실제 도착순(라이브 Kafka)과는 다를 수 있다(단일 계좌 경합의 한계).
ReplacingMergeTree 중복은 FINAL로 정리(오프라인이므로 정확성 우선; --no-final로 생략 가능).
price는 ClickHouse Float64라 라이브의 정확한 Decimal 문자열가와 미세한 정밀도 차가 있을 수 있다.
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
from backtest.models import BTick


def _client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_HTTP_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DB,
    )


def load_ticks(symbols=None, start=None, end=None, use_final=True):
    """ticks를 전역 시간순으로 스트리밍 yield한다.

    symbols: 종목 리스트(None이면 전체). start/end: 'YYYY-MM-DD HH:MM:SS' 등 문자열(UTC, None이면 무제한).
    """
    client = _client()
    final = "FINAL" if use_final else ""
    conds = ["1=1"]
    params: dict = {}
    if symbols:
        conds.append("symbol IN {symbols:Array(String)}")
        params["symbols"] = list(symbols)
    if start:
        conds.append("trade_ts >= parseDateTimeBestEffort({start:String}, 'UTC')")
        params["start"] = start
    if end:
        conds.append("trade_ts < parseDateTimeBestEffort({end:String}, 'UTC')")
        params["end"] = end
    where = " AND ".join(conds)
    query = (
        "SELECT symbol, price, toUnixTimestamp64Milli(trade_ts) AS ts_ms, seq "
        f"FROM ticks {final} WHERE {where} ORDER BY trade_ts, symbol, seq"
    )
    with client.query_row_block_stream(query, parameters=params) as stream:
        for block in stream:
            for row in block:
                yield BTick(symbol=row[0], price=Decimal(str(row[1])), ts=row[2] / 1000.0)
