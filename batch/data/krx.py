"""KR 외부데이터 원본 적재 (단일 책임: KRX 정보데이터시스템 → 수급·외국인보유·공매도).

pykrx 로그인 방식(KRX_ID/KRX_PW). 통계 엔드포인트는 로그인 필수 — 미인증 시 LOGOUT(빈 응답).
종목별 1패스로 stock_investor_flow·stock_foreign_holding·stock_short를 함께 채운다.
종목별 격리(일시 오류 1종목이 나머지를 막지 않음). 재실행 멱등(ReplacingMergeTree).

⚠ pykrx 로그인·.env 선로드는 batch.data._krx_session이 일원화(거기서 stock·require_login import).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.krx [--start 2018-01-01] [--sleep 0.2]
"""
import argparse
import sys
import time
from datetime import date

from batch.data._krx_session import require_login, stock
from common.clickhouse_client import create_client
from common.symbols import get_kr_symbols

# 12분류 중 의미 단위 11종(전체=합계는 파생 가능하므로 제외)
_INVESTOR = {
    "금융투자": "fin_invest", "보험": "insurance", "투신": "invest_trust",
    "사모": "private_fund", "은행": "bank", "기타금융": "other_finance",
    "연기금": "pension", "기타법인": "other_corp", "개인": "individual",
    "외국인": "foreign", "기타외국인": "other_foreign",
}
_FLOW_COLS = ["date", "symbol", "investor", "net_value", "net_volume"]
_HOLD_COLS = ["date", "symbol", "listed_shares", "held_shares", "holding_ratio",
              "limit_shares", "exhaustion_rate"]
_SHORT_COLS = ["date", "symbol", "short_volume", "total_volume", "short_volume_ratio",
               "short_balance_qty", "short_balance_value", "market_cap", "short_balance_ratio"]


def _flow_rows(symbol: str, start: str, end: str) -> list:
    val = stock.get_market_trading_value_by_date(start, end, symbol, detail=True)
    vol = stock.get_market_trading_volume_by_date(start, end, symbol, detail=True)
    if val is None or len(val) == 0:
        return []
    out = []
    for d in val.index:
        vrow = vol.loc[d] if (vol is not None and d in vol.index) else None
        for kr, en in _INVESTOR.items():
            if kr not in val.columns:
                continue
            nv = float(val.at[d, kr])
            nq = float(vrow[kr]) if (vrow is not None and kr in vol.columns) else 0.0
            out.append([d.date(), symbol, en, nv, nq])
    return out


def _holding_rows(symbol: str, start: str, end: str) -> list:
    df = stock.get_exhaustion_rates_of_foreign_investment_by_date(start, end, symbol)
    if df is None or len(df) == 0:
        return []
    return [[d.date(), symbol,
             float(df.at[d, "상장주식수"]), float(df.at[d, "보유수량"]),
             float(df.at[d, "지분율"]), float(df.at[d, "한도수량"]),
             float(df.at[d, "한도소진률"])] for d in df.index]


def _short_rows(symbol: str, start: str, end: str) -> list:
    bal = stock.get_shorting_balance_by_date(start, end, symbol)        # 잔고 2016-06~
    vol = stock.get_shorting_volume_by_date(start, end, symbol)         # 거래량 ~2017~
    dates = set()
    if bal is not None:
        dates |= set(bal.index)
    if vol is not None:
        dates |= set(vol.index)
    out = []
    for d in sorted(dates):
        b = bal.loc[d] if (bal is not None and d in bal.index) else None
        v = vol.loc[d] if (vol is not None and d in vol.index) else None
        out.append([
            d.date(), symbol,
            float(v["공매도"]) if v is not None else 0.0,
            float(v["매수"]) if v is not None else 0.0,
            float(v["비중"]) if v is not None else 0.0,
            float(b["공매도잔고"]) if b is not None else 0.0,
            float(b["공매도금액"]) if b is not None else 0.0,
            float(b["시가총액"]) if b is not None else 0.0,
            float(b["비중"]) if b is not None else 0.0,
        ])
    return out


def store_kr_market(symbols=None, start="2018-01-01", sleep=0.2, log=print):
    require_login()
    end = date.today().strftime("%Y%m%d")
    start = start.replace("-", "")
    ch = create_client()
    if symbols is None:
        symbols = get_kr_symbols(ch)
    if not symbols:
        log("[krx] 대상 종목 없음(stock_candles_1d market='KR' 비어있음).")
        return 0, 0
    nsym, failed = 0, []
    totals = {"flow": 0, "holding": 0, "short": 0}
    for i, sym in enumerate(symbols, 1):
        try:
            flow = _flow_rows(sym, start, end); time.sleep(sleep)
            hold = _holding_rows(sym, start, end); time.sleep(sleep)
            short = _short_rows(sym, start, end); time.sleep(sleep)
            if flow:
                ch.insert("stock_investor_flow", flow, column_names=_FLOW_COLS)
                totals["flow"] += len(flow)
            if hold:
                ch.insert("stock_foreign_holding", hold, column_names=_HOLD_COLS)
                totals["holding"] += len(hold)
            if short:
                ch.insert("stock_short", short, column_names=_SHORT_COLS)
                totals["short"] += len(short)
            nsym += 1
            if i % 50 == 0:
                log(f"[krx] {i}/{len(symbols)}종목... flow={totals['flow']:,} "
                    f"hold={totals['holding']:,} short={totals['short']:,}")
        except Exception as e:
            failed.append(sym)
            log(f"[krx] {sym} 실패(건너뜀): {type(e).__name__}: {e}")
    log(f"[krx] 완료: {nsym}/{len(symbols)}종목 "
        f"(flow={totals['flow']:,} hold={totals['holding']:,} short={totals['short']:,}); "
        f"실패 {len(failed)}: {failed[:10]}")
    return nsym, totals


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="KRX KR 수급·외국인보유·공매도 → ClickHouse")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--symbols", help="쉼표 구분 종목코드(미지정 시 저장된 KR 일봉 전체)")
    p.add_argument("--sleep", type=float, default=0.2, help="호출 간 대기(초)")
    a = p.parse_args(argv)
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()] if a.symbols else None
    store_kr_market(symbols=syms, start=a.start, sleep=a.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
