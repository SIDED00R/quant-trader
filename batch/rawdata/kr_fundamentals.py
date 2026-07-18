"""KR 펀더멘털 원본 적재 (단일 책임: DART OpenAPI → fundamentals_quarterly의 KR 분).

US SEC EDGAR(fundamentals.py)의 KR 대응. US와 동일 concept명·duration 규약으로 적재 →
batch/features/edgar.py가 KR 펀더멘털 피처(PBR·PER·ROA·ROE·PSR)를 시장 구분 없이 자동 생성.
스키마 변경 없음. source='DART'. 재실행 멱등(ReplacingMergeTree).

PIT: filed_date=rcept_no[:8](공시 접수일). 사용 시 filed_date ≤ 거래일 게이팅.
flow 분기화: 분기·반기보고서 thstrm=당기3개월(직접), 연간 thstrm=12개월 → Q4=연간−Q3누적(9개월).
instant(자본·자산)=보고서 기말 잔고. shares=stockTotqySttus 보통주 유통주식수(연 1회).

⚠ DART 한도 ~20,000 req/day → 종목별 1패스·shares 연1회·전 응답 캐싱(재실행 시 재페치 없음).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.rawdata.kr_fundamentals [--start-year 2018] [--symbols 005930,000660]
"""
import argparse
import io
import os
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

import httpx

from common.cache import dump_json, load_json, refcache_path
from common.clickhouse_client import create_client
from common.config import DART_API_KEY
from common.marketdata.symbols import get_kr_symbols

_BASE = "https://opendart.fss.or.kr/api"
_CACHE = os.path.join(os.path.dirname(__file__), ".dart_cache")
_REPRTS = ["11013", "11012", "11014", "11011"]      # 1Q · 반기 · 3Q · 사업보고서
_QEND = {"11013": "0331", "11012": "0630", "11014": "0930", "11011": "1231"}
_COLS = ["symbol", "concept", "period_end", "filed_date", "form", "duration_d", "value"]
# concept → (허용 sj_div, IFRS account_id 집합, 계정명 폴백 집합)
_INST = {
    "assets": ({"BS"}, {"ifrs-full_Assets"}, {"자산총계"}),
    "equity": ({"BS"}, {"ifrs-full_Equity"}, {"자본총계"}),
}
_FLOW = {
    "revenue": ({"IS", "CIS"}, {"ifrs-full_Revenue"}, {"매출액", "영업수익", "수익(매출액)", "매출"}),
    "net_income": ({"IS", "CIS"}, {"ifrs-full_ProfitLoss"}, {"당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"}),
    "op_cashflow": ({"CF"}, {"ifrs-full_CashFlowsFromUsedInOperatingActivities"},
                    {"영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로 인한 현금흐름"}),
}


def _num(s):
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def corp_code_map(key: str, client: httpx.Client) -> dict:
    """stock_code(6자리) → corp_code(8자리). corpCode.xml 다운로드 후 참조캐시(영속)에 저장."""
    fp = refcache_path("corp_map.json")   # 참조캐시(영속 볼륨) — corp_code는 사실상 불변(#218)
    cached = load_json(fp)
    if cached:
        return cached
    r = client.get(f"{_BASE}/corpCode.xml", params={"crtfc_key": key})
    r.raise_for_status()
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(r.content)).read("CORPCODE.xml"))
    m = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = e.findtext("corp_code").strip()
    dump_json(fp, m)
    return m


def _fetch_report(key, client, corp, year, reprt, sleep) -> list:
    """fnlttSinglAcntAll 단일 보고서(CFS 우선, 없으면 OFS). 빈 결과도 캐싱(재페치 방지)."""
    fp = os.path.join(_CACHE, f"{corp}_{year}_{reprt}.json")
    cached = load_json(fp)
    if cached is not None:
        return cached
    out = []
    for fs in ("CFS", "OFS"):
        r = client.get(f"{_BASE}/fnlttSinglAcntAll.json", params={
            "crtfc_key": key, "corp_code": corp, "bsns_year": str(year),
            "reprt_code": reprt, "fs_div": fs})
        time.sleep(sleep)
        j = r.json()
        if j.get("status") == "000" and j.get("list"):
            out = j["list"]
            break
    dump_json(fp, out)
    return out


def _fetch_shares(key, client, corp, year, sleep):
    """stockTotqySttus(사업보고서) 보통주 유통주식수 + 접수일. (값, filed) 또는 None."""
    fp = os.path.join(_CACHE, f"{corp}_{year}_shares.json")
    cached = load_json(fp)
    if cached is None:
        r = client.get(f"{_BASE}/stockTotqySttus.json", params={
            "crtfc_key": key, "corp_code": corp, "bsns_year": str(year), "reprt_code": "11011"})
        time.sleep(sleep)
        cached = r.json().get("list", []) if r.json().get("status") == "000" else []
        dump_json(fp, cached)
    for it in cached:
        if (it.get("se") or "").strip() == "보통주":
            val = _num(it.get("distb_stock_co"))
            filed = it.get("rcept_no", "")[:8]
            return (val, filed) if val and len(filed) == 8 else None
    return None


def _match(items: list, spec: tuple):
    """(sj_div, account_id 집합, 계정명 집합)에 맞는 첫 항목의 (thstrm, add)."""
    sjs, ids, names = spec
    for it in items:
        if it.get("sj_div") in sjs and (it.get("account_id") in ids or it.get("account_nm") in names):
            return _num(it.get("thstrm_amount")), _num(it.get("thstrm_add_amount"))
    return None, None


def _rows_for_symbol(sym, corp, key, client, start_year, sleep) -> list:
    this_year = date.today().year
    rows = []
    for year in range(start_year, this_year + 1):
        reports = {rp: _fetch_report(key, client, corp, year, rp, sleep) for rp in _REPRTS}
        filed = {rp: (items[0]["rcept_no"][:8] if items else None) for rp, items in reports.items()}
        # instant(자본·자산): 각 보고서 기말 잔고
        for concept, spec in _INST.items():
            for rp, items in reports.items():
                if not items:
                    continue
                th, _ = _match(items, spec)
                if th is not None:
                    rows.append([sym, concept, _qend(year, rp), _iso(filed[rp]), "", 0, th])
        # flow: Q1~Q3=각 보고서 thstrm(당기3개월), Q4=연간 thstrm − Q3 누적(9개월)
        for concept, spec in _FLOW.items():
            q3_add = _match(reports.get("11014", []), spec)[1] if reports.get("11014") else None
            for rp in ("11013", "11012", "11014"):
                if not reports[rp]:
                    continue
                th, _ = _match(reports[rp], spec)
                if th is not None:
                    rows.append([sym, concept, _qend(year, rp), _iso(filed[rp]), "", 90, th])
            if reports["11011"]:
                ann, _ = _match(reports["11011"], spec)
                if ann is not None and q3_add is not None:
                    rows.append([sym, concept, _qend(year, "11011"), _iso(filed["11011"]), "", 90, ann - q3_add])
        # shares(연 1회): 보통주 유통주식수
        sh = _fetch_shares(key, client, corp, year, sleep)
        if sh:
            rows.append([sym, "shares", date(year, 12, 31), _iso(sh[1]), "", 0, sh[0]])
    return rows


def _qend(year, rp) -> date:
    mmdd = _QEND[rp]
    return date(year, int(mmdd[:2]), int(mmdd[2:]))


def _iso(yyyymmdd) -> date:
    return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))


def store_kr_fundamentals(symbols=None, start_year=2018, sleep=0.1, log=print):
    key = DART_API_KEY
    if not key:
        raise RuntimeError("DART_API_KEY 미설정(.env)")
    ch = create_client()
    if symbols is None:
        symbols = get_kr_symbols(ch)
    nsym, total, failed = 0, 0, []
    with httpx.Client(timeout=30) as client:
        cmap = corp_code_map(key, client)
        for i, sym in enumerate(symbols, 1):
            corp = cmap.get(sym)
            if not corp:
                failed.append(sym)
                continue
            try:
                rows = _rows_for_symbol(sym, corp, key, client, start_year, sleep)
                if rows:
                    ch.insert("fundamentals_quarterly", [r + ["DART"] for r in rows],
                              column_names=_COLS + ["source"])
                    nsym += 1; total += len(rows)
                if i % 50 == 0:
                    log(f"[kr-fund] {i}/{len(symbols)}종목... {total:,}행")
            except Exception as e:
                failed.append(sym)
                log(f"[kr-fund] {sym} 실패(건너뜀): {type(e).__name__}: {e}")
    if total == 0:
        raise RuntimeError("[kr-fund] 적재 0행 — DART 키/corp 매핑/유니버스 확인(전종목 실패의 조용한 성공 처리 방지)")
    log(f"[kr-fund] 완료: {nsym}/{len(symbols)}종목 {total:,}행 → fundamentals_quarterly(DART); "
        f"매핑실패/오류 {len(failed)}: {failed[:10]}")
    return nsym, total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="DART KR 펀더멘털 → fundamentals_quarterly(source=DART)")
    p.add_argument("--start-year", type=int, default=2018)
    p.add_argument("--symbols", help="쉼표 구분 종목코드(미지정 시 저장된 KR 일봉 전체)")
    p.add_argument("--sleep", type=float, default=0.1, help="호출 간 대기(초)")
    a = p.parse_args(argv)
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()] if a.symbols else None
    store_kr_fundamentals(symbols=syms, start_year=a.start_year, sleep=a.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
