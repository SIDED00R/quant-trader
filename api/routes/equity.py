"""자산 곡선 조회 라우트 (단일 책임: equity_snapshots 시장별 시계열 + 전체(KRW 환산) 서빙).

각 매매 잡이 종료 시 남긴 일 단위 평가자산을 돌려준다 — 코인=로그인 세션 계정,
KR/US=단일 KIS 모의계좌('kis'). TOTAL은 common/equity_series가 합성(US는 FRED usdkrw 환산),
환율이 없으면 TOTAL만 빈 리스트로 내려간다. 날짜=UTC 달력일 ISO(decisions 라우트와 동일 규약).
"""
from fastapi import APIRouter, Depends

from api.security import current_account_id
from common.equity_series import (
    ICHIMOKU_ACCOUNT,
    KIS_ACCOUNT,
    PAPER_MARKETS,
    fetch_market_series,
    fetch_usdkrw,
    merge_total_krw,
)
from common.postgres_client import pool

router = APIRouter(prefix="/equity")


def _days(n: int) -> int:
    return max(1, min(n, 1830))


@router.get("/history")
def equity_history(account_id: str = Depends(current_account_id), days: int = 365):
    days = _days(days)
    with pool.connection() as conn:
        series = {
            "COIN": fetch_market_series(conn, "COIN", account_id, days),
            "KR": fetch_market_series(conn, "KR", KIS_ACCOUNT, days),
            "KR_ICHIMOKU": fetch_market_series(conn, "KR", ICHIMOKU_ACCOUNT, days),  # 일목 페이퍼(비교용)
            "US": fetch_market_series(conn, "US", KIS_ACCOUNT, days),
        }
    try:
        fx = fetch_usdkrw(days)
    except Exception:            # ClickHouse/환율 부재 — 시장별 곡선은 정상, TOTAL만 생략
        fx = []
    total = merge_total_krw({m: p for m, p in series.items() if m not in PAPER_MARKETS}, fx)  # 가상 자금 제외
    out = {
        m: {"currency": "USD" if m == "US" else "KRW",
            "points": [{"date": d.isoformat(), "equity": e, "cash": c} for d, e, c in pts]}
        for m, pts in series.items()
    }
    out["TOTAL"] = {"currency": "KRW",
                    "points": [{"date": d.isoformat(), "equity": v} for d, v in total]}
    out["fx_latest"] = fx[-1][1] if fx else None
    return out
