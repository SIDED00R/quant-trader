"""주간 리밸런싱 멱등 마커 (단일 책임: ISO 주차별 '이번 주 매매 완료' 기록·조회·완료판정).

스케줄을 평일(1–5)로 넓혀 휴장·실패를 다음 평일 재시도해도, 한 주에 **실제 매매는 1회**가 되도록 보장한다.
완료(=그 주 마커 기록) 조건은 '체결기반' — 할 일이 없거나(이미 목표 정렬) 실제 체결이 1건 이상일 때만.
'할 일'은 기본 buys만(US), rebalance_sells=True(KR)면 이탈 매도도 포함.
휴장·일시적 KIS 실패는 체결 0건 → 미기록 → 다음 평일 재시도.

저장소=Postgres weekly_rebalance(market, iso_week PK). 호출 전 pool이 열려 있어야 한다(execute가 open).
"""
from datetime import date

from common.postgres_client import pool


def _iso_week(d: date) -> str:
    """ISO 주차 키 'YYYY-Www'(거래소 로컬 날짜 기준). 월~금 동일 주차 → 그 주 1회만 매매."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_done(market: str, d: date) -> bool:
    """해당 시장이 d가 속한 ISO 주차에 이미 리밸런싱을 완료했는지."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM weekly_rebalance WHERE market=%s AND iso_week=%s",
            (market, _iso_week(d))).fetchone()
    return row is not None


def mark_week_done(market: str, d: date) -> None:
    """해당 시장·주차를 완료로 기록(중복 안전)."""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO weekly_rebalance (market, iso_week) VALUES (%s,%s) "
            "ON CONFLICT (market, iso_week) DO NOTHING", (market, _iso_week(d)))


def completed(plan: dict, placed: list, rebalance_sells: bool = False) -> bool:
    """이번 주 리밸런싱이 '완료'됐는지 — 할 일이 없거나(이미 정렬) 실제 체결이 1건 이상.

    rebalance_sells=True(KR)면 이탈 매도도 '할 일'로 본다. '체결 1건 이상' 기준 유지 —
    일부 거부(수량<1·거래정지)로 주 내내 재시도가 반복되는 것을 막고, 잔여 주문은
    다음 주 build_plan이 보유 기준으로 재산출해 자체 수렴한다.
    """
    if not plan["targets"]:        # 랭킹 산출 실패(데이터 결손 등) → 미완료, 다음 평일 재시도
        return False
    if not plan["buys"] and not (rebalance_sells and plan["sells"]):
        return True                # 할 일 없음 = 이미 목표 정렬
    return any(o.get("filled") for o in placed)
