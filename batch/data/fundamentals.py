"""US 펀더멘털 원본 적재 (단일 책임: SEC EDGAR companyfacts → fundamentals_quarterly).

vintage 보존(정정공시 대비 filed_date별 모든 값 저장) → point-in-time 사용 가능.
계정: shares·equity·assets(instant) + net_income·revenue·op_cashflow(flow, duration 보존).
재실행 멱등(ReplacingMergeTree). 일별 증분: 그냥 재실행하면 새 공시가 추가된다.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.fundamentals [--fetch]
  --fetch 없으면 캐시된 companyfacts만 적재(다른 작업과 SEC rate 충돌 회피).
"""
import argparse
import os
import sys
from datetime import date

import httpx
import pandas as pd

from batch.features import edgar
from common.cache import load_json
from common.clickhouse_client import create_client
from common.constants import SEC_UA_HEADERS
from common.marketdata.symbols import get_us_symbols

_CONCEPTS = {
    "shares": edgar._SHARES, "equity": edgar._EQUITY, "assets": edgar._ASSETS,
    "net_income": edgar._NI, "revenue": edgar._REV,
    "op_cashflow": ["NetCashProvidedByUsedInOperatingActivities",
                    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
}
_COLS = ["symbol", "concept", "period_end", "filed_date", "form", "duration_d", "value"]


def _rows(symbol: str, facts: dict) -> list:
    out = []
    for concept, tags in _CONCEPTS.items():
        for e in edgar._entries(facts, tags):
            if e.get("val") is None or "end" not in e or "filed" not in e:
                continue
            dur = 0
            if e.get("start"):
                dur = max(0, min(65535, (pd.to_datetime(e["end"]) - pd.to_datetime(e["start"])).days))
            out.append([symbol, concept, date.fromisoformat(e["end"]), date.fromisoformat(e["filed"]),
                        e.get("form", ""), dur, float(e["val"])])
    return out


def store_us_fundamentals(symbols=None, fetch: bool = False, log=print):
    cikmap = edgar.ticker_cik_map()
    ch = create_client()
    if symbols is None:
        symbols = get_us_symbols(ch)
    nsym, total = 0, 0
    with httpx.Client(timeout=30, headers=SEC_UA_HEADERS) as hc:
        for sym in symbols:
            cik = cikmap.get(sym.upper())
            if not cik:
                continue
            if fetch:
                facts = edgar.fetch_companyfacts(cik, hc)
            else:
                fp = os.path.join(edgar._CACHE, f"{cik}.json")
                facts = load_json(fp)
            if not facts:
                continue
            r = _rows(sym, facts)
            if r:
                ch.insert("fundamentals_quarterly", r, column_names=_COLS)   # 종목별 증분
                nsym += 1; total += len(r)
                if nsym % 100 == 0:
                    log(f"[fundamentals] {nsym}종목 {total:,}행...")
    if total == 0:
        raise RuntimeError("[fundamentals] 적재 0행 — SEC 캐시/--fetch/유니버스 확인(전종목 실패의 조용한 성공 처리 방지)")
    log(f"[fundamentals] 완료: {nsym}종목 {total:,}행 → fundamentals_quarterly")
    return nsym, total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="SEC EDGAR 펀더멘털 원본 → fundamentals_quarterly")
    p.add_argument("--fetch", action="store_true", help="캐시 없으면 SEC에서 받기(기본: 캐시만)")
    a = p.parse_args(argv)
    store_us_fundamentals(fetch=a.fetch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
