"""관심종목 데일리 차트 푸시 (단일 책임: watchlist → 종목별 봉차트 텔레그램 발송).

코인 데일리 잡 훅(매일 10:00 KST 부팅, trade-vm-startup 코인 분기)에서 실행 — 하루 1회 관심종목 차트.
전 계정 관심종목 합집합을 KR 우선·added_at 순·상한 MAX_SYMBOLS로 뽑아, 로컬 CH stock_candles_1d 우선
(없거나 최신봉>STALE_DAYS면 Toss 폴백)로 일봉을 받아 symbol_chart(KR 주봉+일목·US 일봉)로 렌더 후
기존 유저세션(notify_telegram.send_photo)으로 발송. 종목별 예외 격리·전부 비치명. equity_chart_telegram 선례.

실행: python -m common.chart.watchlist_chart_telegram
"""
import argparse
import sys
from datetime import date

from common import notify_telegram
from common.postgres_client import close_pool, open_pool, pool
from common.chart.symbol_chart import chart_for_symbol

MAX_SYMBOLS = 20
STALE_DAYS = 7


def select_symbols(rows: list) -> list:
    """rows=[(market, symbol, added_at)] → KR 우선·added_at 오름차순·상한 MAX_SYMBOLS. 순수 함수."""
    kr = sorted([r for r in rows if r[0] == "KR"], key=lambda r: r[2])
    us = sorted([r for r in rows if r[0] == "US"], key=lambda r: r[2])
    return (kr + us)[:MAX_SYMBOLS]


def _names(ch, picks: list) -> dict:
    try:
        syms = list({s for _, s, _ in picks})
        rows = ch.query("SELECT symbol, market, name FROM stock_names FINAL WHERE symbol IN {s:Array(String)}",
                        parameters={"s": syms}).result_rows
        return {(r[1], r[0]): r[2] for r in rows}
    except Exception:
        return {}


def load_bars(ch, market: str, symbol: str) -> list:
    """로컬 CH stock_candles_1d FINAL → [(date,o,h,l,c)]. 0행/최신봉 STALE_DAYS 초과면 Toss 폴백."""
    daily = []
    try:
        rows = ch.query(
            "SELECT toDate(window_start), open, high, low, close FROM stock_candles_1d FINAL "
            "WHERE market={m:String} AND symbol={s:String} ORDER BY window_start",
            parameters={"m": market, "s": symbol}).result_rows
        daily = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]
    except Exception as e:
        print(f"[watchlist-charts] {symbol} CH 조회 실패: {type(e).__name__}: {e}")
    if not (daily and (date.today() - daily[-1][0]).days <= STALE_DAYS):     # 없거나 낡음 → Toss 폴백
        from common.marketdata.toss_daily import fetch_daily
        tr = fetch_daily(symbol, 1000 if market == "KR" else 220, log=lambda *a: None)
        if tr:
            daily = [(r[1].date(), r[2], r[3], r[4], r[5]) for r in tr]
    return daily


def send_watchlist_charts() -> int:
    open_pool()
    with pool.connection() as conn:
        rows = conn.execute("SELECT market, symbol, min(added_at) FROM watchlist GROUP BY market, symbol").fetchall()
    picks = select_symbols([(r[0], r[1], r[2]) for r in rows])
    if not picks:
        print("[watchlist-charts] 관심종목 없음 — 발송 생략")
        return 0
    from common.clickhouse_client import create_client
    ch = create_client()
    names = _names(ch, picks)
    sent = 0
    for market, symbol, _ in picks:
        try:
            daily = load_bars(ch, market, symbol)
            if len(daily) < 2:
                print(f"[watchlist-charts] {symbol} 데이터 부족 — 스킵")
                continue
            png, cap = chart_for_symbol(daily, market, symbol, names.get((market, symbol)))
            if notify_telegram.send_photo(png, cap):
                sent += 1
        except Exception as e:
            print(f"[watchlist-charts] {symbol} 실패(비치명): {type(e).__name__}: {e}")
    print(f"[watchlist-charts] {sent}/{len(picks)}종목 발송")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argparse.ArgumentParser(description="관심종목 데일리 차트 텔레그램 푸시").parse_args(argv)
    try:
        return send_watchlist_charts()
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
