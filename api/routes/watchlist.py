"""관심종목 라우트 (단일 책임: 관심종목 등록·해제·검색). 대시보드 탭·데일리 차트 푸시 대상.

`watchlist(account_id, market, symbol)` PG 테이블. 검색은 CH `stock_names`(월간 maintenance 갱신) ILIKE +
정확 티커 합성행 폴백(사전 미시딩·미스여도 티커로 등록 가능). 순수 랭킹은 merge_search_results(테스트 대상).
쓰기 라우트는 stock_orders.py 패턴(pydantic + Depends(current_account_id) + pool.connection).
"""
import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.security import current_account_id
from common.postgres_client import pool

router = APIRouter(prefix="/watchlist")

_KR_CODE = re.compile(r"^\d{6}$")
_US_TICKER = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")
_ch = None


def _client():
    global _ch
    if _ch is None:
        from common.clickhouse_client import create_client
        _ch = create_client()
    return _ch


class ToggleBody(BaseModel):
    market: Literal["KR", "US"]
    symbol: str = Field(min_length=1, max_length=20)


def _norm(s: str) -> str:
    return "".join(str(s or "").split()).lower()


def merge_search_results(rows: list, watched: set, q: str) -> list:
    """CH 행 [(symbol, market, name)] + 관심집합 + 질의 → 랭킹 결과(상위 20, 티커 합성행 포함). 순수 함수."""
    q = q.strip()
    up, nq = q.upper(), _norm(q)

    def rank(r):
        sym, _market, name = r
        if sym.upper() == up:
            return 0
        if _norm(name) == nq:
            return 1
        if _norm(name).startswith(nq):
            return 2
        if sym.upper().startswith(up):
            return 3
        return 4

    uniq = {(r[1], r[0].upper()): r for r in rows}
    ordered = sorted(uniq.values(), key=lambda r: (rank(r), r[0]))
    res = [{"market": r[1], "symbol": r[0], "name": r[2], "watched": (r[1], r[0]) in watched}
           for r in ordered[:20]]
    if not res:                                                  # 사전 히트 0 + 티커 형태 → 합성행(미시딩·미스여도 등록 가능)
        if _KR_CODE.match(q):
            res.insert(0, {"market": "KR", "symbol": q, "name": None, "watched": ("KR", q) in watched})
        elif _US_TICKER.match(q) and not q.isdigit():
            res.insert(0, {"market": "US", "symbol": up, "name": None, "watched": ("US", up) in watched})
    return res[:20]


def _names_of(pairs: list) -> dict:
    """[(market, symbol)] → {(market, symbol): name} — CH stock_names 조회(실패 시 빈 dict)."""
    if not pairs:
        return {}
    try:
        syms = list({s for _, s in pairs})
        rows = _client().query(
            "SELECT symbol, market, name FROM stock_names FINAL WHERE symbol IN {s:Array(String)}",
            parameters={"s": syms}).result_rows
        return {(r[1], r[0]): r[2] for r in rows}
    except Exception:
        return {}


@router.get("")
def list_watchlist(account_id: str = Depends(current_account_id)):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT market, symbol, added_at FROM watchlist WHERE account_id=%s ORDER BY added_at DESC",
            (account_id,)).fetchall()
    names = _names_of([(r[0], r[1]) for r in rows])
    return [{"market": r[0], "symbol": r[1], "name": names.get((r[0], r[1])),
             "added_at": r[2].isoformat()} for r in rows]


@router.post("/toggle")
def toggle(b: ToggleBody, account_id: str = Depends(current_account_id)):
    """있으면 제거, 없으면 추가 → {"watched": bool}."""
    sym = b.symbol.strip().upper() if b.market == "US" else b.symbol.strip()
    with pool.connection() as conn:
        exists = conn.execute("SELECT 1 FROM watchlist WHERE account_id=%s AND market=%s AND symbol=%s",
                              (account_id, b.market, sym)).fetchone()
        if exists:
            conn.execute("DELETE FROM watchlist WHERE account_id=%s AND market=%s AND symbol=%s",
                         (account_id, b.market, sym))
            return {"watched": False}
        conn.execute("INSERT INTO watchlist (account_id, market, symbol) VALUES (%s,%s,%s) "
                     "ON CONFLICT DO NOTHING", (account_id, b.market, sym))
    return {"watched": True}


@router.get("/search")
def search(q: str = "", account_id: str = Depends(current_account_id)):
    q = (q or "").strip()
    if not q:
        return []
    try:
        rows = [(r[0], r[1], r[2]) for r in _client().query(
            "SELECT symbol, market, name FROM stock_names FINAL "
            "WHERE positionCaseInsensitive(name, {q:String}) > 0 OR symbol ILIKE {p:String} LIMIT 50",
            parameters={"q": q, "p": q + "%"}).result_rows]
    except Exception:
        rows = []
    with pool.connection() as conn:
        w = conn.execute("SELECT market, symbol FROM watchlist WHERE account_id=%s", (account_id,)).fetchall()
    return merge_search_results(rows, {(r[0], r[1]) for r in w}, q)
