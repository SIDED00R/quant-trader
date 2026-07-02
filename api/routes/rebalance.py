"""주간 리밸런싱 상태 라우트 (단일 책임: 이번주 KR/US 자동매매 완료 여부 노출).

주식탭 상단 칩용 — '이번주 자동매매가 실제로 됐는지'를 사이트에서 즉시 확인한다
(2026-07 초 VM 기동실패로 매매가 조용히 유실됐던 사고의 가시성 부재 해소).
weekly_rebalance 마커는 trade-once류가 체결 성공 시에만 기록(trading.strategy.weekly_marker).
"""
from fastapi import APIRouter

from common.market_holidays import market_today
from common.postgres_client import pool
from trading.strategy.weekly_marker import _iso_week

router = APIRouter(prefix="/stocks")


@router.get("/rebalance-status")
def rebalance_status():
    """{"KR": {"iso_week", "done", "done_at"}, "US": {...}} — 거래소 로컬 날짜 기준 이번주."""
    out = {}
    with pool.connection() as conn:
        for market in ("KR", "US"):
            week = _iso_week(market_today(market))
            row = conn.execute(
                "SELECT done_at FROM weekly_rebalance WHERE market=%s AND iso_week=%s",
                (market, week),
            ).fetchone()
            out[market] = {
                "iso_week": week,
                "done": row is not None,
                "done_at": row[0].isoformat() if row else None,
            }
    return out
