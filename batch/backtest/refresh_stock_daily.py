"""주식 일봉 증분 갱신 (단일 책임: 매매 잡 직전 stock_candles_1d 활성 종목 최신화).

유니버스 = 테이블에서 최근 RECENT_DAYS 내 봉이 있는 symbol(상폐 종목 자연 탈락, 신규 종목
편입은 backfill_stock_daily 수동 실행). 종목별 실패 격리 + 벽시계 상한 — 부분 실패의 최종
방어는 스코어링 쪽 신선도·커버리지 게이트(stock_score/stock_trade_common)가 맡는다.
stock_candles_1d는 ReplacingMergeTree라 재실행 멱등(스위퍼 이중 실행 무해).
"""
import time

from batch.backtest.toss_daily import fetch_daily, upsert_clickhouse
from common.clickhouse_client import create_client

DAYS = 14           # 증분 창 — 연휴·직전 실패 며칠을 덮고도 남는 폭
RECENT_DAYS = 30    # 활성 종목 판정: 이 기간 내 봉 존재
MAX_SECONDS = 600   # 벽시계 상한 — 초과 시 잔여 종목 스킵(매매 잡 시간 예산 보호)


def alive_symbols(market: str) -> list:
    """해당 시장에서 최근 RECENT_DAYS 내 봉이 있는 종목(활성 유니버스)."""
    rows = create_client().query(
        "SELECT DISTINCT symbol FROM stock_candles_1d WHERE market={m:String} "
        "AND window_start >= now() - INTERVAL {d:UInt32} DAY ORDER BY symbol",
        parameters={"m": market, "d": RECENT_DAYS}).result_rows
    return [r[0] for r in rows]


def refresh(markets: list, days: int = DAYS, max_seconds: int = MAX_SECONDS, log=print) -> dict:
    """시장별 활성 종목 증분 upsert. 반환 {market: {"ok","fail","rows","skipped"}}."""
    client = create_client()
    t0 = time.monotonic()
    out = {}
    for mk in markets:
        syms = alive_symbols(mk)
        ok = fail = rows = skipped = 0
        for i, s in enumerate(syms):
            if time.monotonic() - t0 > max_seconds:
                skipped = len(syms) - i
                log(f"[refresh] {mk}: 시간상한 {max_seconds}s 초과 — 잔여 {skipped}종목 스킵")
                break
            try:
                rows += upsert_clickhouse(client, fetch_daily(s, days, log=lambda *a: None))
                ok += 1
            except Exception as e:
                fail += 1
                log(f"[refresh] {mk} {s}: {type(e).__name__}: {e}")
        out[mk] = {"ok": ok, "fail": fail, "rows": rows, "skipped": skipped}
        log(f"[refresh] {mk}: {ok}/{len(syms)}종목 {rows}행 갱신" + (f" (실패 {fail})" if fail else ""))
    return out
