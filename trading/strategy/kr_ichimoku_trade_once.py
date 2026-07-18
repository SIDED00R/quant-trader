"""KR 일목균형표 주간 페이퍼 매매 (단일 책임: 주봉 구름 신호 → Postgres 시뮬 체결).

기존 ML 챔피언 전략(stock_trade_once, KIS 모의계좌)과 **병행**하는 검증용 페이퍼 전략이다.
모의계좌가 1개뿐이라 실주문은 내지 않고, 코인 시뮬 장부(paper_ledger→apply_execution)에 체결을 기록한다.
계정 'kr_ichimoku'(auto_trade=FALSE, 시드 1억). 주간 마커 키 'KR_ICHIMOKU'(ML의 'KR'과 격리).

신호(주봉, 9/26/52·선행26, 무룩어헤드=진행 주 부분봉 제외):
  진입 = 종가>구름상단 AND 전환>기준 · 청산 = --exit-rule 택일(tk_cross=전환<기준 데드크로스[기본] / cloud=종가<=구름상단).
캡 있는 실운용 적응: 최대 max-positions 종목, 종목당 예산=평가자산/max, 슬롯 초과 시 돌파강도 상위 우선.
체결가 = 최신 일봉 종가(백테스트=주 종가와 미세 차이 — 월요일 실행분이라 문서화). 무재시도·동기.
매매 후 신규 매수(구름 돌파 진입) 종목의 주봉+일목 차트를 텔레그램 사진으로 발송한다(비치명, /차트 봇 렌더러 재사용).

Dockerfile.batch(trade) 전용 — batch.backtest.refresh_stock_daily/toss_daily 의존(app 이미지 제외 경로).
"""
import argparse
import sys
import traceback
from decimal import Decimal

from batch.backtest.refresh_stock_daily import alive_symbols, refresh
from common import notify_telegram
from common.clickhouse_client import create_client
from common.config import FEE_RATE, STOCK_SELL_TAX_RATE
from common.equity.equity_snapshot import ICHIMOKU_ACCOUNT, record_snapshot
from common.marketdata.ichimoku import latest_signal, weekly_bars
from common.marketdata.market_holidays import is_market_holiday, market_today
from common.postgres_client import close_pool, open_pool, pool
from common.marketdata.stock_ohlc import daily_ohlc
from common.marketdata.stock_price import latest_closes
from trading.portfolio import paper_ledger
from trading.strategy.notify_messages import error_message, ichimoku_message
from trading.strategy.weekly_marker import mark_week_done, week_done

MARKET_KEY = "KR_ICHIMOKU"     # 주간 마커 키(ML 'KR'과 격리)
HISTORY_DAYS = 800             # 선행B 52주 + 선행이동 26주 커버 + 여유
MAX_STALE_DAYS = 7             # 최신 일봉 신선도 상한(초과 시 매매 중단)
STALE_HOLD_DAYS = 14           # 보유 종목이 이 기간 무봉이면 강제 청산(상폐 등 — 장부 부패 방지)
_BUY_COST = Decimal(1) + FEE_RATE
_SELL_KEEP = Decimal(1) - (FEE_RATE + STOCK_SELL_TAX_RATE)


def _skip(reason: str) -> dict:
    """가드 skip 반환 — main의 요약·텔레그램 문안이 깨지지 않는 기본 키 포함(stock_trade_common와 독립)."""
    return {"bar": None, "cash": 0.0, "targets": [], "buys": [], "sells": [], "placed": [], "skipped": reason}


def _px(sym, closes, sig):
    """실행가 = 최신 일봉 종가(우선), 없으면 주봉 신호 종가 폴백."""
    p = closes.get(sym)
    if p and p > 0:
        return float(p)
    return float(sig["close"]) if sig and sig.get("close") else None


def build_signals(hist: dict, today, exit_rule: str) -> dict:
    """{sym: latest_signal(...)} — 구름/전환/기준 미정의 종목은 제외(신호 None)."""
    out = {}
    for sym, rows in hist.items():
        sig = latest_signal(weekly_bars(rows, drop_week_of=today), exit_rule=exit_rule)
        if sig is not None:
            out[sym] = sig
    return out


def plan_trades(signals: dict, held: dict, cash_amt: Decimal, closes: dict,
                last_bar: dict, today, max_positions: int) -> dict:
    """순수 결정부(테스트 대상). 보유·현금·신호·시세 → sells/buys 계획.

    1) 청산(전량): 보유 중 exit 신호 / 신호소멸 / STALE_HOLD_DAYS 무봉 → 매도. 시세 없으면 유지(다음 재시도).
    2) 매수: entry 신호 & 미보유 → 돌파강도(breakout_pct) 내림차순 → 빈 슬롯 채움.
       예산 = 평가자산/max_positions, 매도 대금 매수여력 반영, 정수주(수수료 헤드룸), qty<1 스킵.
    """
    sells, keep = [], {}
    for sym, qty in held.items():
        sig = signals.get(sym)
        px = _px(sym, closes, sig)
        stale = (sym not in last_bar) or ((today - last_bar[sym]).days > STALE_HOLD_DAYS)
        if (sig is None or sig.get("exit") or stale) and px:
            sells.append((sym, qty, px))
        else:
            keep[sym] = qty     # 신호 유지 or 시세 없어 청산 불가(다음 실행 재시도)

    avail = cash_amt + sum(Decimal(str(q)) * Decimal(str(p)) * _SELL_KEEP for _, q, p in sells)
    kept_val = sum(Decimal(str(q)) * Decimal(str(closes.get(s, 0) or 0)) for s, q in keep.items())
    equity = avail + kept_val
    per_name = equity / Decimal(max(1, max_positions))
    free = max_positions - len(keep)

    sold = {s for s, _, _ in sells}
    cands = sorted(
        [(s, sig) for s, sig in signals.items() if sig.get("entry") and s not in keep and s not in sold],
        key=lambda x: (-x[1]["breakout_pct"], x[0]))
    buys, remaining = [], avail
    for sym, sig in cands:
        if len(buys) >= free:
            break
        px = _px(sym, closes, sig)
        if not px:
            continue
        px_d = Decimal(str(px))
        budget = min(per_name, remaining)
        qty = int(budget / (px_d * _BUY_COST))
        if qty < 1:
            continue
        buys.append((sym, qty, px))
        remaining -= Decimal(qty) * px_d * _BUY_COST

    return {"sells": sells, "buys": buys, "keep": keep,
            "targets": [s for s, _ in cands]}


def _refresh_held(live: bool) -> None:
    """스킵 날 일일 스냅샷 신선도 확보 — 보유 종목만 증분 갱신(≤max 종목, 비치명)."""
    if not live:
        return
    with pool.connection() as conn:
        held = list(paper_ledger.positions(conn, ICHIMOKU_ACCOUNT))
    if not held:
        return
    from batch.backtest.toss_daily import fetch_daily, upsert_clickhouse
    client = create_client()
    for s in held:
        try:
            upsert_clickhouse(client, fetch_daily(s, 14, log=lambda *a: None))
        except Exception as e:
            print(f"[kr-ichimoku] {s} 보유종목 갱신 실패(비치명): {type(e).__name__}: {e}")


def execute(live: bool, max_positions: int, exit_rule: str) -> dict:
    """가드 → (트레이드 데이) 신선도·신호·계획·시뮬 체결·마커. 반환=요약 dict(stock_message 호환)."""
    open_pool()
    today = market_today("KR")
    if week_done(MARKET_KEY, today):
        _refresh_held(live)
        return _skip(f"이미 이번주 리밸런싱 완료({today})")
    if is_market_holiday("KR", today):
        _refresh_held(live)
        return _skip(f"KR 휴장일({today}) — 다음 평일 재시도")

    refresh(["KR"], log=print)
    universe = alive_symbols("KR")
    hist = daily_ohlc("KR", universe, HISTORY_DAYS)
    last_bar = {s: rows[-1][0] for s, rows in hist.items() if rows}
    if not last_bar:
        raise RuntimeError("stock_candles_1d(KR) 이력 없음 — backfill_stock_daily 확인")
    freshest = max(last_bar.values())
    if (today - freshest).days > MAX_STALE_DAYS:
        raise RuntimeError(f"stock_candles_1d(KR) 신선도 초과 — 최신봉 {freshest}"
                           f"(경과 {(today - freshest).days}일 > {MAX_STALE_DAYS}) — 갱신/백필 확인")

    signals = build_signals(hist, today, exit_rule)
    with pool.connection() as conn:
        held = paper_ledger.positions(conn, ICHIMOKU_ACCOUNT)
        cash_amt = paper_ledger.cash(conn, ICHIMOKU_ACCOUNT)
    relevant = set(held) | {s for s, sig in signals.items() if sig.get("entry")}
    closes = latest_closes("KR", list(relevant))
    p = plan_trades(signals, held, cash_amt, closes, last_bar, today, max_positions)

    placed = []
    if live:
        for side, orders in (("SELL", p["sells"]), ("BUY", p["buys"])):
            for sym, qty, px in orders:                 # 매도 먼저(대금 매수여력 반영) → 매수
                with pool.connection() as conn:
                    res = paper_ledger.simulate_fill(conn, ICHIMOKU_ACCOUNT, sym, side, qty, Decimal(str(px)))
                placed.append({"symbol": sym, "side": side, "qty": qty,
                               "accepted": res == "applied", "filled": res == "applied",
                               "filled_qty": qty if res == "applied" else 0, "msg": res})
        mark_week_done(MARKET_KEY, today)               # 페이퍼 체결은 동기·결정적 → 완료 기록(0건=이미 정렬)

    bought = [o["symbol"] for o in placed if o.get("side") == "BUY" and o.get("filled")]   # 실제 체결분만
    return {"bar": str(freshest), "cash": float(cash_amt),
            "targets": p["targets"], "buys": [s for s, _, _ in p["buys"]],
            "sells": [s for s, _, _ in p["sells"]], "placed": placed, "skipped": None,
            "buy_bars": [(s, hist[s]) for s in bought if hist.get(s)]}   # 체결된 신규 매수 차트용 일봉


def snapshot() -> None:
    """페이퍼 계좌 평가자산 1행 upsert — 평일 매일(스킵 날 포함) 1포인트. 비치명."""
    with pool.connection() as conn:
        held = paper_ledger.positions(conn, ICHIMOKU_ACCOUNT)
        cash_amt = paper_ledger.cash(conn, ICHIMOKU_ACCOUNT)
    closes = latest_closes("KR", list(held)) if held else {}
    pos_val = sum(Decimal(str(q)) * Decimal(str(closes.get(s, 0) or 0)) for s, q in held.items())
    record_snapshot("KR", ICHIMOKU_ACCOUNT, "KRW", cash_amt + pos_val,
                    cash=cash_amt, positions_value=pos_val)


def send_entry_charts(buy_bars: list) -> None:
    """신규 매수(구름 돌파 진입) 종목의 주봉+일목 차트를 텔레그램 사진으로 발송(종목별 격리·전부 비치명).

    buy_bars=[(symbol, 일봉 rows)] — execute()가 채운다. /차트 봇과 동일한 symbol_chart 렌더러 재사용.
    """
    if not buy_bars:
        return
    try:                                                        # 셋업 실패도 비치명(체결은 이미 끝난 뒤)
        from common.marketdata import stock_names
        from common.chart.symbol_chart import chart_for_symbol
        idx = stock_names.build_index(stock_names.fetch_all())
    except Exception as e:
        print(f"[kr-ichimoku] 차트 셋업 실패(비치명): {type(e).__name__}: {e}")
        return
    for sym, bars in buy_bars:
        try:
            hit = stock_names.resolve(idx, sym)
            name = hit[2] if hit and hit[2] != sym else None
            png, cap = chart_for_symbol(bars, "KR", sym, name)
            notify_telegram.send_photo(png, "🟢 [KR 일목 매수] " + cap)
        except Exception as e:
            print(f"[kr-ichimoku] {sym} 차트 발송 실패(비치명): {type(e).__name__}: {e}")


def main(argv=None) -> int:
    """진입점(스케줄러가 호출). 종료코드 0=정상 / 70=오류(텔레그램 통보) / 1=통보도 실패(startup 폴백)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="KR 일목 주간 페이퍼 매매(시뮬 장부)")
    ap.add_argument("--max-positions", type=int, default=30)
    ap.add_argument("--exit-rule", choices=["tk_cross", "cloud"], default="tk_cross")
    ap.add_argument("--live", action="store_true", help="시뮬 체결·마커·스냅샷 기록(미지정=계획만)")
    a = ap.parse_args(argv)
    try:
        r = execute(live=a.live, max_positions=a.max_positions, exit_rule=a.exit_rule)
        if a.live:
            snapshot()
    except Exception as e:
        traceback.print_exc()
        sent = notify_telegram.send(error_message("KR 일목(페이퍼)", e))
        return 70 if sent else 1
    finally:
        close_pool()
    print(f"[kr-ichimoku] bar={r['bar']} cash={r['cash']:,.0f} "
          f"targets={len(r['targets'])} buys={len(r['buys'])} sells={len(r['sells'])} live={a.live}")
    if r.get("skipped"):
        print(f"  skip: {r['skipped']}")
    for o in r.get("placed", []):
        print("  ", o)
    notify_telegram.send(ichimoku_message("KR 일목(페이퍼)", r, live=a.live))
    if a.live:
        send_entry_charts(r.get("buy_bars") or [])   # 매수 종목 주봉+일목 차트 발송(비치명)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
