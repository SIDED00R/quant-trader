"""종목명 사전 (단일 책임: 티커↔이름 조회·질의 해석). app 이미지 안전(httpx만).

US=SEC company_tickers.json(신뢰) · KR=KRX 정보데이터시스템 전종목 기본정보(비공식·best-effort).
정확한 티커 입력은 사전 없이도 `resolve`가 처리한다 — 사전 로딩이 실패해도 `/차트 005930`·`/차트 AAPL`은
항상 동작하고, KR 이름검색(`/차트 삼성전자`)만 열화된다. 시장별 fetch는 서로 독립 실패 허용.
"""
import re

import httpx

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
KRX_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_UA = "quant-trader/1.0 (research; contact via repo)"
_KR_CODE = re.compile(r"^\d{6}$")
_US_TICKER = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def _norm(s: str) -> str:
    return "".join(str(s).split()).lower()


def fetch_us_names() -> list[tuple[str, str]]:
    """[(ticker, name)] — SEC company_tickers.json(title). User-Agent 필수(SEC 정책)."""
    r = httpx.get(SEC_TICKERS_URL, headers={"User-Agent": _UA}, timeout=20)
    r.raise_for_status()
    out = []
    for v in r.json().values():
        t, nm = str(v.get("ticker", "")).strip().upper(), str(v.get("title", "")).strip()
        if t and nm:
            out.append((t, nm))
    return out


def fetch_kr_names() -> list[tuple[str, str]]:
    """[(6자리코드, 한글약명)] — KRX 전종목 기본정보(코스피 STK + 코스닥 KSQ). 비공식 엔드포인트."""
    out = []
    for mkt in ("STK", "KSQ"):
        r = httpx.post(KRX_URL, headers={"User-Agent": _UA, "Referer": "http://data.krx.co.kr/"},
                       data={"bld": "dbms/MDC/STAT/standard/MDCSTAT01901", "mktId": mkt}, timeout=20)
        r.raise_for_status()
        for row in r.json().get("OutBlock_1", []):
            code, nm = str(row.get("ISU_SRT_CD", "")).strip(), str(row.get("ISU_ABBRV", "")).strip()
            if _KR_CODE.match(code) and nm:
                out.append((code, nm))
    return out


def fetch_all() -> dict:
    """{"KR": [...], "US": [...]} — 시장별 독립 실패 허용(한쪽 실패가 다른 쪽 안 지움)."""
    out = {"KR": [], "US": []}
    for mkt, fn in (("US", fetch_us_names), ("KR", fetch_kr_names)):
        try:
            out[mkt] = fn()
        except Exception as e:
            print(f"[stock-names] {mkt} 조회 실패(비치명): {type(e).__name__}: {e}")
    return out


def build_index(names: dict) -> dict:
    """검색 인덱스: by_symbol(대문자 티커→행) · by_name(정규화명→행) · rows[(market,symbol,name)]."""
    rows, by_symbol, by_name = [], {}, {}
    for market, pairs in names.items():
        for sym, nm in pairs:
            row = (market, sym.upper() if market == "US" else sym, nm)
            rows.append(row)
            by_symbol[row[1].upper()] = row
            by_name.setdefault(_norm(nm), row)
    return {"rows": rows, "by_symbol": by_symbol, "by_name": by_name}


def resolve(index: dict, q: str):
    """질의 → (market, symbol, name) 또는 None. 우선순위: 정확심볼 > 정확명 > 유일 prefix > 유일 substring.

    사전에 없어도 티커 형태면 합성행 반환 — 정확 티커 경로는 사전 무관하게 항상 동작.
    """
    q = q.strip()
    if not q:
        return None
    up, nq = q.upper(), _norm(q)
    if index:
        if up in index["by_symbol"]:
            return index["by_symbol"][up]
        if nq in index["by_name"]:
            return index["by_name"][nq]
        pref = [r for r in index["rows"] if _norm(r[2]).startswith(nq)]
        if len(pref) == 1:
            return pref[0]
        sub = [r for r in index["rows"] if nq in _norm(r[2])]
        if len(sub) == 1:
            return sub[0]
    # 사전 미스 → 티커 형태면 직행(KR 6자리 / US 알파)
    if _KR_CODE.match(q):
        return ("KR", q, q)
    if _US_TICKER.match(q) and not q.isdigit():
        return ("US", up, up)
    return None
