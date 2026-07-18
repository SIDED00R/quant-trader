"""KR 미시구조 고속 전체이력 적재 (단일 책임: KRX by-date 벌크 → 수급·공매도·외국인보유).

krx.py(per-symbol, 투자자 12분류 상세·증분용)의 보완 — **날짜별 전종목 함수**로 전 이력을
빠르게 채운다(per-symbol은 종목당 장기범위가 느려 전이력 ~수시간; 본 모듈은 ~분 단위).
수급은 핵심 3집계(외국인·기관합계·연기금)만(피처가 쓰는 단위). 동일 테이블·스키마, 재실행 멱등.

⚠ pykrx 로그인·.env 선로드는 batch.data._krx_session이 일원화(거기서 stock·require_login import).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.krx_bulk [--start 2018-01-01]
"""
import argparse
import sys
from datetime import date

from batch.data._krx_session import require_login, stock
from common.clickhouse_client import create_client
from common.marketdata.symbols import get_kr_symbols

_FLOW_INV = {"외국인": "foreign", "기관합계": "institution", "연기금": "pension"}
_FLOW_COLS = ["date", "symbol", "investor", "net_value", "net_volume"]
_HOLD_COLS = ["date", "symbol", "listed_shares", "held_shares", "holding_ratio", "limit_shares", "exhaustion_rate"]
_SHORT_COLS = ["date", "symbol", "short_volume", "total_volume", "short_volume_ratio",
               "short_balance_qty", "short_balance_value", "market_cap", "short_balance_ratio"]


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _trading_days(start: str, end: str) -> list:
    """KOSPI 지수(1001) 일봉 인덱스 = KR 거래일(캔들 비의존, 1콜)."""
    idx = stock.get_index_ohlcv(start.replace("-", ""), end.replace("-", ""), "1001").index
    return [d.date() for d in idx]


def _day_rows(ds: str, d: date, univ: set):
    flow, hold, short = [], [], []
    for kinv, einv in _FLOW_INV.items():
        df = stock.get_market_net_purchases_of_equities_by_ticker(ds, ds, "ALL", kinv)
        for t in df.index:
            if t in univ:
                flow.append([d, t, einv, _f(df.at[t, "순매수거래대금"]), _f(df.at[t, "순매수거래량"])])
    eh = stock.get_exhaustion_rates_of_foreign_investment_by_ticker(ds, "ALL")
    for t in eh.index:
        if t in univ:
            hold.append([d, t, _f(eh.at[t, "상장주식수"]), _f(eh.at[t, "보유수량"]), _f(eh.at[t, "지분율"]),
                         _f(eh.at[t, "한도수량"]), _f(eh.at[t, "한도소진률"])])
    vol, bal = {}, {}
    for mkt in ("KOSPI", "KOSDAQ"):
        for t, r in stock.get_shorting_volume_by_ticker(ds, mkt).iterrows():
            if t in univ:
                vol[t] = (_f(r["공매도"]), _f(r["매수"]), _f(r["비중"]))
        for t, r in stock.get_shorting_balance_by_ticker(ds, mkt).iterrows():
            if t in univ:
                bal[t] = (_f(r["공매도잔고"]), _f(r["공매도금액"]), _f(r["시가총액"]), _f(r["비중"]))
    for t in set(vol) | set(bal):
        v = vol.get(t, (0, 0, 0)); b = bal.get(t, (0, 0, 0, 0))
        short.append([d, t, v[0], v[1], v[2], b[0], b[1], b[2], b[3]])
    return flow, hold, short


def store_kr_bulk(start="2018-01-01", end=None, flush_every=50, log=print):
    require_login()
    ch = create_client()
    univ = set(get_kr_symbols(ch))
    if not univ:
        log("[krx-bulk] 대상 종목 없음(stock_candles_1d market='KR' 비어있음).")
        return 0
    days = _trading_days(start, end or date.today().strftime("%Y-%m-%d"))
    log(f"[krx-bulk] 거래일 {len(days)}일 ({days[0]}~{days[-1]}), 유니버스 {len(univ)}종목")
    fb, hb, sb, n, fail = [], [], [], 0, 0

    def flush():
        if fb:
            ch.insert("stock_investor_flow", [r + ["KRX"] for r in fb], column_names=_FLOW_COLS + ["source"])
            ch.insert("stock_foreign_holding", [r + ["KRX"] for r in hb], column_names=_HOLD_COLS + ["source"])
            ch.insert("stock_short", [r + ["KRX"] for r in sb], column_names=_SHORT_COLS + ["source"])

    for i, d in enumerate(days, 1):
        try:
            f, h, s = _day_rows(d.strftime("%Y%m%d"), d, univ)
            fb += f; hb += h; sb += s; n += 1
        except Exception as e:
            fail += 1
            log(f"[krx-bulk] {d} 실패(건너뜀): {type(e).__name__}: {e}")
            continue
        if i % flush_every == 0:
            flush()
            log(f"[krx-bulk] {i}/{len(days)}일... (flow {len(fb):,})")
            fb, hb, sb = [], [], []
    flush()
    log(f"[krx-bulk] 완료: {n}/{len(days)}일 (실패 {fail})")
    return n


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="KRX by-date 벌크 → 수급·공매도·외국인보유(고속 전체이력)")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default=None, help="종료일(미지정 시 오늘). 갭 백필용")
    a = p.parse_args(argv)
    store_kr_bulk(start=a.start, end=a.end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
