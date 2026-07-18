"""US 공매도 잔고 적재 (단일 책임: FINRA 격주 통합 공매도 → stock_short market='US').

FINRA Equity Short Interest(정산일별 공매도 잔고 포지션, 격주). 2021-06+ 거래소상장+OTC 통합.
KR(KRX)과 같은 stock_short 테이블에 market='US'로 적재 — 교차시장 공매도 신호 완성.
키없음(공개 CDN). 파일명=정산일(shrtYYYYMMDD.csv). 재실행 멱등(ReplacingMergeTree).
주의: FINRA는 '잔고 포지션'만 — 공매도 거래량(short_volume)은 별개 일간 파일이라 여기선 0.
      total_volume=평균 일거래량(KR의 당일 총거래량과 의미 다름, 잔고/평균거래량=days-to-cover용).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.rawdata.finra_short [--days 75]
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta

import httpx

from batch.rawdata._parse import to_float as _flt
from common.clickhouse_client import create_client
from common.constants import SEC_UA_HEADERS
from common.marketdata.symbols import get_us_symbols

_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{ymd}.csv"
# stock_short 삽입 컬럼(KR 전용 컬럼은 0, market/source 명시로 DEFAULT KR/KRX 덮어씀)
_COLS = ["date", "symbol", "short_volume", "total_volume", "short_volume_ratio",
         "short_balance_qty", "short_balance_value", "market_cap", "short_balance_ratio",
         "market", "source"]


def _parse(csv_text: str, universe: set) -> list:
    """FINRA 파이프구분 CSV → 우리 유니버스 stock_short 행(market='US')."""
    lines = csv_text.splitlines()
    if not lines:
        return []
    idx = {h.strip(): i for i, h in enumerate(lines[0].split("|"))}
    c_sym = idx.get("symbolCode")
    c_qty = idx.get("currentShortPositionQuantity")
    c_adv = idx.get("averageDailyVolumeQuantity")
    c_set = idx.get("settlementDate")
    if c_sym is None or c_qty is None or c_set is None:
        return []
    maxi = max(i for i in (c_sym, c_qty, c_adv, c_set) if i is not None)   # 최고 인덱스 기준(컬럼 순서 무관)
    out = []
    for line in lines[1:]:
        p = line.split("|")
        if len(p) <= maxi:
            continue
        sym = p[c_sym].strip().upper()
        if sym not in universe:
            continue
        try:
            d = datetime.strptime(p[c_set].strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        adv = _flt(p[c_adv]) if c_adv is not None else 0.0
        out.append([d, sym, 0.0, adv, 0.0, _flt(p[c_qty]), 0.0, 0.0, 0.0, "US", "FINRA"])
    return out


def collect(days: int = 75, log=print) -> int:
    ch = create_client()
    universe = set(get_us_symbols(ch))
    if not universe:
        raise RuntimeError("[finra] US 유니버스 없음(stock_candles_1d market='US' 비어있음)")
    today = date.today()
    total, files = 0, 0
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers=SEC_UA_HEADERS) as c:
        for n in range(days + 1):                          # 최근 days일 정산일 후보 스캔(404 스킵)
            d = today - timedelta(days=n)
            r = c.get(_URL.format(ymd=d.strftime("%Y%m%d")))
            if r.status_code != 200:
                time.sleep(0.1)                            # 404 스캔도 과속 금지(예절)
                continue
            rows = _parse(r.text, universe)
            if rows:
                ch.insert("stock_short", rows, column_names=_COLS)
                total += len(rows)
            files += 1
            log(f"[finra] {d}: {len(rows):,}종목")
            time.sleep(0.3)
    if files == 0:
        raise RuntimeError(f"[finra] 최근 {days}일 정산파일 0개 — CDN URL/일정 확인")
    if total == 0:                                         # 파일은 받았으나 0행 → 헤더/필드명 변경 의심(형제 수집기와 통일)
        raise RuntimeError("[finra] 파일은 받았으나 적재 0행 — CSV 헤더/필드명 확인")
    log(f"[finra] 완료: 파일 {files}개, {total:,}행 → stock_short(market='US')")
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="FINRA US 공매도 → stock_short(market='US')")
    p.add_argument("--days", type=int, default=75, help="정산일 후보 스캔 기간(최근 N일)")
    a = p.parse_args(argv)
    collect(a.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
