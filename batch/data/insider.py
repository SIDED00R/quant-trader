"""내부자 거래 적재 (단일 책임: SEC DERA insider-transactions 분기 데이터셋 → insider_transactions).

SEC Form 3/4/5의 XML 기재부를 평탄화한 분기 zip(SUBMISSION/REPORTINGOWNER/NONDERIV_TRANS.tsv).
우리 US 유니버스만 필터(ISSUERTRADINGSYMBOL로 티커 직접 획득 → 13F와 달리 OpenFIGI 불필요).
비파생(NONDERIV) 거래만 적재 — 공개시장 매수(P)/매도(S)가 핵심 내부자 신호. filed_date로 PIT 게이팅.
키없음(SEC User-Agent 예절만). 재실행 멱등(ReplacingMergeTree). 헤더명 기반 파싱(컬럼 순서 변동 견고).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.insider [--start-year 2019]
"""
import argparse
import io
import os
import sys
import time
import zipfile
from datetime import date, datetime

import httpx

from common.clickhouse_client import create_client
from common.constants import SEC_UA_HEADERS
from common.marketdata.symbols import get_us_symbols

_CACHE = os.path.join(os.path.dirname(__file__), ".insider_cache")
_BASE = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets"
_COLS = ["symbol", "trans_date", "filed_date", "accession", "trans_sk", "owner_cik",
         "relationship", "trans_code", "acquired_disp", "shares", "price", "shares_owned_after"]


def _parse_date(s: str):
    """SEC 날짜(DD-MON-YYYY / YYYY-MM-DD / YYYYMMDD) → date. 실패 시 None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _reader(zf: zipfile.ZipFile, suffix: str):
    """zip 멤버(대소문자 무시 endswith) → (헤더 index맵, 라인 iterator). 없으면 (None, None)."""
    name = next((n for n in zf.namelist() if n.upper().endswith(suffix)), None)
    if not name:
        return None, None
    f = io.TextIOWrapper(zf.open(name), encoding="latin1")
    header = f.readline().rstrip("\n").split("\t")
    idx = {h.strip().upper(): i for i, h in enumerate(header)}
    return idx, f


def _get(parts: list, idx: dict, key: str) -> str:
    i = idx.get(key)
    return parts[i].strip() if i is not None and i < len(parts) else ""


def _fetch_zip(year: int, q: int, client: httpx.Client):
    """분기 zip 다운로드(캐시, 손상 재다운로드). 미공개(404/403)는 None."""
    os.makedirs(_CACHE, exist_ok=True)
    url = f"{_BASE}/{year}q{q}_form345.zip"
    fp = os.path.join(_CACHE, f"{year}q{q}_form345.zip")
    for _ in (1, 2):
        if not os.path.exists(fp):
            r = client.get(url)
            if r.status_code in (403, 404):
                return None                               # 미공개 분기
            r.raise_for_status()
            open(fp, "wb").write(r.content)
        try:
            return zipfile.ZipFile(fp)
        except zipfile.BadZipFile:
            os.remove(fp)
    return None


def _relationship(parts: list, idx: dict) -> str:
    """RPTOWNER_RELATIONSHIP 텍스트(예: 'Director, Officer'/'TenPercentOwner') → 상위 관계 하나."""
    rel = _get(parts, idx, "RPTOWNER_RELATIONSHIP").lower()
    if "director" in rel:
        return "director"
    if "officer" in rel:
        return "officer"
    if "tenpercent" in rel:
        return "tenpct"
    return "other"


def _quarter_rows(zf: zipfile.ZipFile, universe: set) -> list:
    """한 분기 zip → 우리 유니버스 비파생 거래 행."""
    sidx, sf = _reader(zf, "SUBMISSION.TSV")
    if sidx is None:
        return []
    sub = {}                                              # accession → (symbol, filed_date)
    with sf:
        for line in sf:
            p = line.rstrip("\n").split("\t")
            sym = _get(p, sidx, "ISSUERTRADINGSYMBOL").upper()
            if sym not in universe:
                continue
            acc = _get(p, sidx, "ACCESSION_NUMBER")
            sub[acc] = (sym, _parse_date(_get(p, sidx, "FILING_DATE")))
    if not sub:
        return []

    ridx, rf = _reader(zf, "REPORTINGOWNER.TSV")
    owner = {}                                            # accession → (owner_cik, relationship)
    if ridx is not None:
        with rf:
            for line in rf:
                p = line.rstrip("\n").split("\t")
                acc = _get(p, ridx, "ACCESSION_NUMBER")
                if acc in sub and acc not in owner:       # 첫 보고자 채택
                    owner[acc] = (_get(p, ridx, "RPTOWNERCIK"), _relationship(p, ridx))

    tidx, tf = _reader(zf, "NONDERIV_TRANS.TSV")
    if tidx is None:
        return []
    rows = []
    with tf:
        for line in tf:
            p = line.rstrip("\n").split("\t")
            acc = _get(p, tidx, "ACCESSION_NUMBER")
            meta = sub.get(acc)
            if meta is None:
                continue
            sym, filed = meta
            tdate = _parse_date(_get(p, tidx, "TRANS_DATE"))
            if tdate is None:
                continue
            ocik, rel = owner.get(acc, ("", "other"))
            rows.append([
                sym, tdate, filed or tdate, acc,
                _get(p, tidx, "NONDERIV_TRANS_SK"), ocik, rel,
                _get(p, tidx, "TRANS_CODE"), _get(p, tidx, "TRANS_ACQUIRED_DISP_CD"),
                _flt(_get(p, tidx, "TRANS_SHARES")), _flt(_get(p, tidx, "TRANS_PRICEPERSHARE")),
                _flt(_get(p, tidx, "SHRS_OWND_FOLWNG_TRANS")),
            ])
    return rows


def _flt(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def collect(start_year: int = 2019, log=print) -> int:
    ch = create_client()
    universe = set(get_us_symbols(ch))
    if not universe:
        raise RuntimeError("[insider] US 유니버스 없음(stock_candles_1d market='US' 비어있음)")
    this_year = date.today().year
    total = 0
    with httpx.Client(timeout=180, headers=SEC_UA_HEADERS, follow_redirects=True) as c:
        for year in range(start_year, this_year + 1):
            for q in (1, 2, 3, 4):
                zf = _fetch_zip(year, q, c)
                if zf is None:
                    continue
                rows = _quarter_rows(zf, universe)
                if rows:
                    ch.insert("insider_transactions", rows, column_names=_COLS)
                    total += len(rows)
                log(f"[insider] {year}Q{q}: {len(rows):,}행")
                time.sleep(0.5)
    if total == 0:
        raise RuntimeError("[insider] 적재 0행 — SEC 데이터셋 URL/포맷/유니버스 확인")
    log(f"[insider] 완료: {total:,}행 → insider_transactions")
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="SEC Form 4 내부자 거래 → insider_transactions")
    p.add_argument("--start-year", type=int, default=2019)
    a = p.parse_args(argv)
    collect(a.start_year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
