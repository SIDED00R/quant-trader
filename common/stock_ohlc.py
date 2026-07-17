"""주식 일봉 이력 로더 (단일 책임: stock_candles_1d → 종목별 (date, o, h, l, c) 시계열).

app 이미지 안전(batch.* 비의존) — 일목 페이퍼 매매·차트가 공용으로 쓴다.
stock_candles_1d는 ReplacingMergeTree라 FINAL로 중복 제거(latest_closes·macro_daily 선례).
"""
from common.clickhouse_client import create_client


def daily_ohlc(market: str, symbols: list, days: int) -> dict:
    """{symbol: [(date, open, high, low, close)]} 오름차순. 최근 days일 창."""
    if not symbols:
        return {}
    rows = create_client().query(
        "SELECT symbol, toDate(window_start) AS d, open, high, low, close "
        "FROM stock_candles_1d FINAL "
        "WHERE market={m:String} AND symbol IN {syms:Array(String)} "
        "AND window_start >= now() - INTERVAL {d:UInt32} DAY "
        "ORDER BY symbol, d",
        parameters={"m": market, "syms": list(symbols), "d": days}).result_rows
    out: dict = {}
    for sym, d, o, h, l, c in rows:
        out.setdefault(sym, []).append((d, float(o), float(h), float(l), float(c)))
    return out
