"""주식 최신 종가 조회 (단일 책임: ClickHouse stock_candles_1d → 시장별 최신 일봉 종가).

stock_trade_common(배치 매매)에서 이동 — batch.* 비의존이라 app 이미지(api 등)에서도
import 가능하다(프로덕션 임포트 경계 준수). KIS 현재가가 없을 때의 폴백 시세로도 쓴다.
"""
from common.clickhouse_client import create_client


def latest_closes(market: str, symbols: list) -> dict:
    """해당 시장의 최신 일봉 종가(symbol→close). 동일가중 수량 산정용."""
    if not symbols:
        return {}
    rows = create_client().query(
        "SELECT symbol, argMax(close, window_start) FROM stock_candles_1d "
        "WHERE market={mkt:String} AND symbol IN {syms:Array(String)} GROUP BY symbol",
        parameters={"mkt": market, "syms": list(symbols)}).result_rows
    return {r[0]: float(r[1]) for r in rows}
