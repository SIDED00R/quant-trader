"""candles_1d 조회 (단일 책임: ClickHouse 일봉 종가 스트림).

라이브 전략 워커(live_ensemble)와 API(strategy 라우트)가 공용으로 쓴다 — **backtest 패키지 비의존**
(프로덕션 이미지엔 backtest가 없다). 오프라인 백테스트용 풍부한 로더는 backtest.datasource가 별도 담당.
ReplacingMergeTree 중복은 FINAL로 정리. (symbol, close:Decimal, ts:epoch초) 전역 시간순 yield.
"""
from decimal import Decimal

from common.clickhouse_client import create_client


def daily_candles(symbols):
    """candles_1d를 (symbol, close, ts) 전역 시간순(window_start, symbol)으로 yield(종가 기준)."""
    client = create_client()
    query = (
        "SELECT symbol, close, toUnixTimestamp64Milli(toDateTime64(window_start, 3)) AS ts_ms "
        "FROM candles_1d FINAL WHERE symbol IN {syms:Array(String)} ORDER BY window_start, symbol"
    )
    with client.query_row_block_stream(query, parameters={"syms": list(symbols)}) as stream:
        for block in stream:
            for row in block:
                yield (row[0], Decimal(str(row[1])), row[2] / 1000.0)
