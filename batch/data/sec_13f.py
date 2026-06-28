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


def _cusip_to_ticker(cusips: list, universe: set, client: httpx.Client, log=print) -> dict:
    """CUSIP→ticker (OpenFIGI ID_CUSIP, 검증됨·안정). 우리 유니버스 종목만 반환. 캐시.
    키없음 25/min·10/batch → 상위 CUSIP만 매핑."""
    os.makedirs(_CACHE, exist_ok=True)
    fp = os.path.join(_CACHE, "cusip_map.json")
    cache = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {}
    todo = [c for c in cusips if c not in cache]
    for i in range(0, len(todo), 10):
        b = [{"idType": "ID_CUSIP", "idValue": cu} for cu in todo[i:i + 10]]
        r = client.post("https://api.openfigi.com/v3/mapping", json=b)
        if r.status_code == 200:
            for cu, d in zip(todo[i:i + 10], r.json()):
                tk = d["data"][0].get("ticker") if d.get("data") else None
                cache[cu] = tk
        time.sleep(2.5)
        if (i + 10) % 200 == 0:
            log(f"[13f] cusip→ticker {i+10}/{len(todo)}")
    json.dump(cache, open(fp, "w"), ensure_ascii=False)
    return {cu: tk for cu, tk in cache.items() if tk in universe}


def _zip_urls(client: httpx.Client, start_year: int) -> list:
    """날짜범위(최근)·분기형식(구형식) ZIP URL 모두 파싱 → [(url, period_end)] (start_year+)."""
    r = client.get(_LIST)
    urls = re.findall(r'href="(/files/[^"]*form-13f-data-sets/[^"]+\.zip)"', r.text)
    out = []
    for u in urls:
        name = u.split("/")[-1]
        m = re.search(r"-(\d{2})([a-z]{3})(\d{4})_form13f", name)        # 날짜범위(종료일)
        q = re.search(r"(\d{4})q(\d)_form13f", name)                     # 분기형식
        if m:
            pe = date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        elif q:
            qn = int(q.group(2))
            pe = date(int(q.group(1)), qn * 3, [31, 30, 30, 31][qn - 1])  # q1..q4 분기말
        else:
            continue
        if pe.year >= start_year:
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
    universe = set(us)
    with httpx.Client(timeout=180, headers=_UA, follow_redirects=True) as c:
        quarters = _zip_urls(c, start_year)
        log(f"[13f] 분기 {len(quarters)}개")
        # 부트스트랩: 최근 '완성'분기에서 CUSIP별 보유기관수 집계 → 상위 CUSIP을 CUSIP→ticker 매핑.
        # (보유기관 많은 상위 CUSIP = 대형주 = 우리 유니버스 포함. CUSIP은 전분기 공통 키.)
        cutoff = date.today() - timedelta(days=50)
        boot_q = ([q for q in quarters if q[1] <= cutoff] or quarters)[-1]
        zf, it = _fetch_zip(boot_q[0], c)
        holders = {}
        with zf.open(it) as f:
            f.readline()
            for raw in io.TextIOWrapper(f, encoding="latin1"):
                p = raw.rstrip("\n").split("\t")
                if len(p) < 10 or p[9].strip():        # 주식만(옵션 제외)
                    continue
                holders.setdefault(p[4], set()).add(p[0])
        top = sorted(holders, key=lambda cu: -len(holders[cu]))[:3000]
        cusip2tk = _cusip_to_ticker(top, universe, c, log)
        cusips = set(cusip2tk)
        log(f"[13f] 부트스트랩 CUSIP {len(cusips)}/{len(universe)}종목 (상위 {len(top)} 매핑)")
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
