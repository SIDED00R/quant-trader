"""실적 발표일 캘린더 적재 (단일 책임: SEC EDGAR submissions → earnings_calendar market='US').

US 실적 발표일 = 8-K Item 2.02(Results of Operations) 접수일. SEC submissions API의 filings.recent에서
form=='8-K' & items에 '2.02' 포함 필터 → filingDate가 실제 발표일. 키없음(SEC 예절만).
용도: 실적 전후 매매 마스킹(갭 리스크) + 실적발표후표류(PEAD) 이벤트. 재실행 멱등(ReplacingMergeTree).
recent 블록(최근 ~1년)만 조회 — 월간 재실행으로 히스토리 누적. KR(DART rcept_dt)은 추후 확장(TODO).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.earnings
"""
import argparse
import sys
import time
from datetime import datetime

import httpx

from batch.features.edgar import ticker_cik_map
from common.clickhouse_client import create_client
from common.constants import SEC_USER_AGENT
from common.symbols import get_us_symbols

_UA = {"User-Agent": SEC_USER_AGENT}
_SUB = "https://data.sec.gov/submissions/CIK{cik}.json"
_COLS = ["symbol", "market", "announce_date", "period_end", "form", "source"]


def _date(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _earnings_rows(symbol: str, recent: dict) -> list:
    """filings.recent(병렬 배열) → 8-K Item 2.02 실적발표 행."""
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    reports = recent.get("reportDate", [])
    out = []
    for i, form in enumerate(forms):
        item_str = items[i] if i < len(items) else ""
        if form != "8-K" or "2.02" not in [x.strip() for x in item_str.split(",")]:   # 콤마 토큰 정확매칭
            continue
        ad = _date(dates[i] if i < len(dates) else "")
        if ad is None:
            continue
        pe = _date(reports[i] if i < len(reports) else "")
        out.append([symbol, "US", ad, pe, "8-K(2.02)", "SEC"])
    return out


def collect(log=print) -> int:
    ch = create_client()
    universe = get_us_symbols(ch)
    if not universe:
        raise RuntimeError("[earnings] US 유니버스 없음(stock_candles_1d market='US' 비어있음)")
    cik_map = ticker_cik_map()
    total, hit, miss = 0, 0, 0
    with httpx.Client(timeout=30, headers=_UA, follow_redirects=True) as c:
        for i, sym in enumerate(universe, 1):
            cik = cik_map.get(sym.upper())
            if not cik:
                miss += 1
                continue
            time.sleep(0.12)                               # SEC ≤10req/s 예절
            r = c.get(_SUB.format(cik=cik))
            if r.status_code != 200:
                continue
            rows = _earnings_rows(sym, r.json().get("filings", {}).get("recent", {}))
            if rows:
                ch.insert("earnings_calendar", rows, column_names=_COLS)
                total += len(rows)
                hit += 1
            if i % 100 == 0:
                log(f"[earnings] {i}/{len(universe)}종목... {total:,}행")
    if total == 0:
        raise RuntimeError("[earnings] 적재 0행 — SEC submissions/유니버스 확인")
    log(f"[earnings] 완료: {hit}종목 {total:,}행 (CIK미매핑 {miss}) → earnings_calendar")
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argparse.ArgumentParser(description="SEC 8-K 실적발표일 → earnings_calendar").parse_args(argv)
    collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
