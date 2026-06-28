"""US 13F 기관보유 적재 (단일 책임: SEC DERA Form13F → institutional_13f).

우리 512종목만 필터링(메모리 효율). 부트스트랩: 유니버스 ticker→FIGI(OpenFIGI)→최근분기 INFOTABLE에서
FIGI→CUSIP 역추적 → CUSIP 집합(CUSIP은 구분기에도 항상 존재, FIGI는 최근만). 분기별 INFOTABLE을
CUSIP로 스트리밍 필터 후 집계: 보유기관수(distinct accession)·총주식수(SH)·총평가액. 옵션(PUTCALL) 제외.
주의: VALUE 단위가 2023Q1부터 천$→$ 변경 → total_shares·num_holders가 robust 신호(VALUE는 보조).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.sec_13f [--start-year 2019]
"""
import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()
from common.clickhouse_client import create_client

_UA = {"User-Agent": "coin-auto-trader research jh.lee@kornukopia-ai.com"}
_CACHE = os.path.join(os.path.dirname(__file__), ".13f_cache")
_LIST = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _universe_figi(tickers: list, client: httpx.Client, log=print) -> dict:
    """ticker→compositeFIGI (OpenFIGI, 캐시). 키없음 25/min·10/batch."""
    os.makedirs(_CACHE, exist_ok=True)
    fp = os.path.join(_CACHE, "figi_map.json")
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    out = {}
    for i in range(0, len(tickers), 10):
        b = [{"idType": "TICKER", "idValue": t, "exchCode": "US"} for t in tickers[i:i + 10]]
        r = client.post("https://api.openfigi.com/v3/mapping", json=b)
        if r.status_code == 200:
            for t, d in zip(tickers[i:i + 10], r.json()):
                if d.get("data"):
                    out[t] = d["data"][0].get("compositeFIGI") or d["data"][0].get("figi")
        time.sleep(2.5)
        if (i + 10) % 100 == 0:
            log(f"[13f] figi {i+10}/{len(tickers)}")
    json.dump(out, open(fp, "w"), ensure_ascii=False)
    return out


def _zip_urls(client: httpx.Client, start_year: int) -> list:
    r = client.get(_LIST)
    urls = re.findall(r'href="(/files/structureddata/data/form-13f-data-sets/[^"]+\.zip)"', r.text)
    out = []
    for u in urls:
        m = re.search(r"-(\d{2})([a-z]{3})(\d{4})_form13f", u)
        if m and int(m.group(3)) >= start_year:
            pe = date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
            out.append(("https://www.sec.gov" + u, pe))
    return sorted(out, key=lambda x: x[1])


def _fetch_zip(url: str, client: httpx.Client):
    """분기 ZIP을 캐시에서 또는 다운로드. 손상 캐시는 재다운로드. (zf, infotable_member) 반환."""
    fn = url.split("/")[-1]
    fp = os.path.join(_CACHE, fn)
    for attempt in (1, 2):
        if not os.path.exists(fp):
            r = client.get(url)
            r.raise_for_status()
            open(fp, "wb").write(r.content)
        try:
            zf = zipfile.ZipFile(fp)
            it = next((n for n in zf.namelist() if n.upper().endswith("INFOTABLE.TSV")), None)
            if it:
                return zf, it
            return zf, None                            # 구조 변형: INFOTABLE 없음
        except zipfile.BadZipFile:
            os.remove(fp)                              # 손상 캐시 → 재다운로드
    return None, None


def _aggregate(zf: zipfile.ZipFile, infotable: str, cusips: set) -> dict:
    """INFOTABLE 스트리밍 → CUSIP별 {holders:set, shares, value} (우리 CUSIP만, 옵션 제외)."""
    agg = {}
    with zf.open(infotable) as f:
        f.readline()                                    # 헤더
        for raw in io.TextIOWrapper(f, encoding="latin1"):
            p = raw.rstrip("\n").split("\t")
            if len(p) < 9 or p[4] not in cusips or p[9].strip():   # CUSIP idx4, PUTCALL idx9(옵션 제외)
                continue
            a = agg.setdefault(p[4], {"h": set(), "sh": 0.0, "v": 0.0})
            a["h"].add(p[0])
            try:
                a["v"] += float(p[6] or 0)
                if p[8].strip().upper() == "SH":
                    a["sh"] += float(p[7] or 0)
            except ValueError:
                pass
    return agg


def collect(start_year: int = 2019, log=print) -> int:
    ch = create_client()
    us = [r[0] for r in ch.query("SELECT DISTINCT symbol FROM stock_candles_1d WHERE market='US' ORDER BY symbol").result_rows]
    total = 0
    with httpx.Client(timeout=180, headers=_UA, follow_redirects=True) as c:
        figi = _universe_figi(us, c, log)
        figi2tk = {v: k for k, v in figi.items()}
        quarters = _zip_urls(c, start_year)
        log(f"[13f] 유니버스 FIGI {len(figi)}, 분기 {len(quarters)}개")
        # 부트스트랩: 최근분기에서 FIGI→CUSIP→ticker (CUSIP은 전분기 공통 키)
        recent, rit = _fetch_zip(quarters[-1][0], c)
        cusip2tk = {}
        with recent.open(rit) as f:
            f.readline()
            for raw in io.TextIOWrapper(f, encoding="latin1"):
                p = raw.rstrip("\n").split("\t")
                if len(p) >= 6 and p[5] in figi2tk:
                    cusip2tk[p[4]] = figi2tk[p[5]]
        cusips = set(cusip2tk)
        log(f"[13f] 부트스트랩 CUSIP {len(cusips)}/{len(us)}종목")
        for url, pe in quarters:
            zf, it = _fetch_zip(url, c)
            if zf is None or it is None:
                log(f"[13f] {pe}: INFOTABLE 없음/손상 — 건너뜀")
                continue
            agg = _aggregate(zf, it, cusips)
            rows = [[cusip2tk[cu], pe, len(a["h"]), a["sh"], a["v"]] for cu, a in agg.items() if cu in cusip2tk]
            if rows:
                ch.insert("institutional_13f", rows,
                          column_names=["symbol", "period_end", "num_holders", "total_shares", "total_value"])
                total += len(rows)
            log(f"[13f] {pe}: {len(rows)}종목 적재")
    log(f"[13f] 완료: {total}행 → institutional_13f")
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="SEC DERA 13F → institutional_13f")
    p.add_argument("--start-year", type=int, default=2019)
    a = p.parse_args(argv)
    collect(a.start_year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
