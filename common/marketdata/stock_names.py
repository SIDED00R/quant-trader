"""종목명 사전 (단일 책임: 티커↔이름 조회·질의 해석). app 이미지 안전(런타임 외부호출 0).

종목명 사전은 repo에 번들된 `common/marketdata/refdata/stock_names.json`을 로드한다 — FinanceDataReader로 KR 전종목
(KRX) + US(NASDAQ/NYSE/AMEX)를 1회 생성해 커밋한 정적 맵이다. KRX/SEC 실시간 엔드포인트가 세션·UA
제약으로 불안정(각각 400 LOGOUT·403)해 런타임 의존을 제거했다. 갱신은 `scripts/refresh_stock_names.py`
재실행(상장 변화는 느려 가끔이면 충분). 정확한 티커/코드 입력은 사전 없이도 `resolve`가 처리한다 —
사전 로딩이 실패해도 `/차트 005930`·`/차트 AAPL`은 항상 동작하고, 이름검색(`/차트 삼성전자`)만 열화된다.
"""
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_BUNDLED = os.path.join(os.path.dirname(__file__), "refdata", "stock_names.json")
_KR_CODE = re.compile(r"^\d{6}$")
_US_TICKER = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")

# 통칭/영문 → KRX 코드. 상장약명이 통칭·영문과 달라(NAVER·NC·삼성전자 등) 이름검색이 빗나가는 대형주 보정.
# 키는 정규화형(소문자·공백제거)과 일치해야 한다. 필요 종목은 여기에 한 줄씩 추가.
_ALIASES = {
    "네이버": "035420",
    "엔씨소프트": "036570", "엔씨": "036570", "ncsoft": "036570",
    "삼성": "005930", "samsung": "005930",
    "sk하이닉스": "000660", "하이닉스": "000660", "hynix": "000660",
    "카카오": "035720", "kakao": "035720",
    "카카오뱅크": "323410", "카뱅": "323410",
    "현대차": "005380", "현대자동차": "005380", "hyundai": "005380",
    "포스코": "005490", "posco": "005490",
    "엘지에너지솔루션": "373220", "lg에너지솔루션": "373220",
    "기아": "000270", "kia": "000270",
}


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
        logger.error(f"번들 사전 로드 실패(비치명): {type(e).__name__}: {e}")
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
    """검색 인덱스: rows[(market, symbol, name)] — resolve가 KR 우선으로 순회 해석."""
    rows = []
    for market, pairs in names.items():
        for sym, nm in pairs:
            rows.append((market, sym.upper() if market == "US" else sym, nm))
    return {"rows": rows}


def resolve(index: dict, q: str):
    """질의 → (market, symbol, name) 또는 None.

    우선순위(**KR 우선** — 한국 사용자 기준, US 티커가 KR 종목명을 가리는 문제 방지):
    구어별칭 > (KR 정확심볼 > KR 정확명) > (US 정확심볼 > US 정확명) > 유일 prefix(KR>US) > 유일 substring(KR>US).
    사전에 없어도 코드/티커 형태면 합성행 반환 — 정확 코드/티커는 사전 무관하게 항상 동작.
    """
    q = q.strip()
    if not q:
        return None
    up, nq = q.upper(), _norm(q)
    rows = index.get("rows", []) if index else []
    # 0) 통칭/영문 별칭(네이버·엔씨소프트·samsung 등) → KR 코드
    code = _ALIASES.get(nq)
    if code:
        for r in rows:
            if r[0] == "KR" and r[1] == code:
                return r
        return ("KR", code, q)
    # 1) 정확 매칭 — KR 우선(심볼 > 이름), 그다음 US
    for market in ("KR", "US"):
        for r in rows:                                          # 정확 심볼/코드
            if r[0] == market and r[1].upper() == up:
                return r
        for r in rows:                                          # 정확 이름
            if r[0] == market and _norm(r[2]) == nq:
                return r
    # 2) 유일 prefix → 유일 substring (각각 KR 우선)
    for pred in (str.startswith, lambda n, s: s in n):
        for market in ("KR", "US"):
            hit = [r for r in rows if r[0] == market and pred(_norm(r[2]), nq)]
            if len(hit) == 1:
                return hit[0]
    # 사전 미스 → 코드/티커 형태면 직행(KR 6자리 / US 알파)
    if _KR_CODE.match(q):
        return ("KR", q, q)
    if _US_TICKER.match(q):                                     # 첫 글자가 알파벳이라 숫자열과 배타
        return ("US", up, up)
    return None
