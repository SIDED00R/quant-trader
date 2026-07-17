"""종목명 사전 (단일 책임: 티커↔이름 조회·질의 해석). app 이미지 안전(런타임 외부호출 0).

종목명 사전은 repo에 번들된 `common/refdata/stock_names.json`을 로드한다 — FinanceDataReader로 KR 전종목
(KRX) + US(NASDAQ/NYSE/AMEX)를 1회 생성해 커밋한 정적 맵이다. KRX/SEC 실시간 엔드포인트가 세션·UA
제약으로 불안정(각각 400 LOGOUT·403)해 런타임 의존을 제거했다. 갱신은 `scripts/refresh_stock_names.py`
재실행(상장 변화는 느려 가끔이면 충분). 정확한 티커/코드 입력은 사전 없이도 `resolve`가 처리한다 —
사전 로딩이 실패해도 `/차트 005930`·`/차트 AAPL`은 항상 동작하고, 이름검색(`/차트 삼성전자`)만 열화된다.
"""
import json
import os
import re

_BUNDLED = os.path.join(os.path.dirname(__file__), "refdata", "stock_names.json")
_KR_CODE = re.compile(r"^\d{6}$")
_US_TICKER = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def _norm(s: str) -> str:
    return "".join(str(s).split()).lower()


def fetch_all() -> dict:
    """{"KR": [(코드, 이름)], "US": [(티커, 이름)]} — repo 번들 사전 로드(런타임 외부호출 0).

    파일 부재·파손 시 빈 사전 반환(비치명 — 티커/코드 경로는 resolve가 사전 없이도 처리).
    """
    try:
        with open(_BUNDLED, encoding="utf-8") as f:
            b = json.load(f)
        return {"KR": [(str(c), str(nm)) for c, nm in b.get("KR", [])],
                "US": [(str(t).upper(), str(nm)) for t, nm in b.get("US", [])]}
    except Exception as e:
        print(f"[stock-names] 번들 사전 로드 실패(비치명): {type(e).__name__}: {e}")
        return {"KR": [], "US": []}


def refresh_clickhouse(ch) -> int:
    """fetch_all → ClickHouse stock_names upsert(ReplacingMergeTree 멱등). 적재 행수. ch=create_client()."""
    names = fetch_all()
    rows = [[sym.upper() if market == "US" else sym, market, nm, "FDR"]
            for market, pairs in names.items() for sym, nm in pairs]
    if not rows:
        return 0
    ch.insert("stock_names", rows, column_names=["symbol", "market", "name", "source"])
    return len(rows)


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
    if _US_TICKER.match(q):                                     # 첫 글자가 알파벳이라 숫자열과 배타
        return ("US", up, up)
    return None
