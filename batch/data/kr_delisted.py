"""KR 상장폐지 종목 수집 (단일 책임: FDR KRX-DELISTING → stock_delisting + 상폐 OHLCV).

생존편향 보정: 현 유니버스가 생존 종목만이라 절대 IC 과대 → 상폐 종목의 폐지일·사유 +
종목별 일봉(미수정 raw)을 적재해 PIT 유니버스(상장~폐지 구간)로 백테스트 편향을 줄인다.
메타는 stock_delisting, OHLCV는 stock_candles_1d(market='KR')에 합류(폐지일 게이팅은 메타로).
재실행 멱등(ReplacingMergeTree). 가격은 미수정(액면분할 등 별도 보정 필요).

⚠ FDR는 data.krx.co.kr 스크레이핑이라 깨지기 쉬움 — 라이브러리 최신 유지. 전체 시세 백필은
종목당 HTTP 호출이라 수천종목 = 장시간(일회성). 메타만은 빠름(목록 1콜).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.kr_delisted [--limit N] [--no-prices]
"""
import argparse
import sys
from datetime import datetime, timezone

import FinanceDataReader as fdr
import pandas as pd

from common.clickhouse_client import create_client
from common.constants import COLUMNS_STOCK_CANDLES_1D

_DELIST_COLS = ["symbol", "name", "market", "listing_date", "delisting_date", "reason"]


def _date(x):
    """문자열/Timestamp → date(또는 None)."""
    try:
        d = pd.to_datetime(x)
        return d.date() if pd.notna(d) else None
    except Exception:
        return None


def store_delisting(ch, log=print) -> list:
    """FDR KRX-DELISTING 목록 → stock_delisting. 폐지일 있는 종목코드 리스트 반환."""
    df = fdr.StockListing("KRX-DELISTING")
    rows, syms = [], []
    for _, r in df.iterrows():
        sym = str(r.get("Symbol") or "").strip()
        dd = _date(r.get("DelistingDate"))
        if not sym or dd is None:
            continue
        ld = _date(r.get("ListingDate"))
        rows.append([sym, str(r.get("Name") or ""), str(r.get("Market") or ""),
                     ld or dd, dd, str(r.get("Reason") or "")])
        syms.append(sym)
    if not rows:
        raise RuntimeError("[delisted] 적재 0행 — FDR KRX-DELISTING 스크레이핑 확인(전체 실패의 조용한 성공 처리 방지)")
    ch.insert("stock_delisting", rows, column_names=_DELIST_COLS)
    log(f"[delisted] 상폐 메타 {len(rows)}종목 → stock_delisting")
    return syms


def store_prices(ch, symbols: list, log=print) -> int:
    """상폐 종목별 일봉(미수정) → stock_candles_1d(market='KR'). 종목 격리(1종목 실패가 막지 않음)."""
    total, failed = 0, 0
    for i, sym in enumerate(symbols, 1):
        try:
            df = fdr.DataReader(sym, exchange="KRX-DELISTING")
        except Exception:
            failed += 1
            continue
        if df is None or len(df) == 0:
            continue
        # window_start = 거래일(KST 날짜 라벨) 00:00 UTC — 기존 stock_candles_1d 규약과 동일.
        # tz-aware UTC로 넣어야 naive→KST 변환(-9h, 날짜 어긋남)을 막는다.
        rows = [[sym, datetime(idx.year, idx.month, idx.day, tzinfo=timezone.utc),
                 float(r.Open), float(r.High), float(r.Low), float(r.Close), float(r.Volume), "KRW", "KR"]
                for idx, r in df.iterrows()]
        ch.insert("stock_candles_1d", rows, column_names=COLUMNS_STOCK_CANDLES_1D)
        total += len(rows)
        if i % 100 == 0:
            log(f"[delisted] {i}/{len(symbols)}종목... {total:,}행 (실패 {failed})")
    log(f"[delisted] 시세 {total:,}행 → stock_candles_1d(market='KR'); 실패 {failed}")
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="KR 상폐종목(FDR) → stock_delisting + OHLCV(생존편향 보정)")
    p.add_argument("--limit", type=int, default=0, help="시세 적재 종목 수 제한(0=전체, 검증용 소수 지정)")
    p.add_argument("--no-prices", action="store_true", help="메타만 적재(시세 생략, 빠름)")
    a = p.parse_args(argv)
    ch = create_client()
    syms = store_delisting(ch)
    if not a.no_prices:
        store_prices(ch, syms[:a.limit] if a.limit else syms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
