"""시장별 평가자산 스냅샷 기록 (단일 책임: equity_snapshots 하루 1행 upsert — 매매 잡 종료부 훅).

스냅샷은 부가 기능이라 어떤 실패도 매매 잡을 깨뜨리지 않는다 — 모든 예외를 삼키고 False를 반환.
같은 날 재실행(스위퍼·US 다중 부팅)은 PK(market, account_id, snap_date) upsert로 마지막 실행이 승리.
KR/US는 단일 KIS 모의계좌라 account_id='kis' 고정(accounts FK 없음 — 의도, db/migrations/postgres/0001_baseline.sql).
"""
import logging
from datetime import datetime, timezone

from common.postgres_client import open_pool, pool

logger = logging.getLogger(__name__)

KIS_ACCOUNT = "kis"
ICHIMOKU_ACCOUNT = "kr_ichimoku"   # KR 일목 페이퍼 전략(실주문 없는 시뮬 장부) — market='KR'·별도 account_id로 곡선 분리

_UPSERT = (
    "INSERT INTO equity_snapshots (snap_date, market, account_id, currency, cash, positions_value, equity) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (market, account_id, snap_date) DO UPDATE SET "
    "ts=now(), currency=EXCLUDED.currency, cash=EXCLUDED.cash, "
    "positions_value=EXCLUDED.positions_value, equity=EXCLUDED.equity"
)


def record_snapshot(market: str, account_id: str, currency: str, equity,
                    cash=None, positions_value=None) -> bool:
    """평가자산 1행 upsert. 실패는 내부 흡수(로그만) — 매매 잡의 종료코드에 영향 없음."""
    try:
        open_pool()   # 멱등 — 콜러가 이미 열었어도 무해
        snap_date = datetime.now(timezone.utc).date()
        with pool.connection() as conn:
            conn.execute(_UPSERT, (snap_date, market, account_id, currency, cash, positions_value, equity))
        logger.info(f"{market}/{account_id} {snap_date} equity={equity}")
        return True
    except Exception as e:
        logger.error(f"기록 실패(비치명 — 매매 결과 무관): {type(e).__name__}: {e}")
        return False


def record_stock_snapshot(market: str, balance_fn) -> bool:
    """KIS 잔고 재조회 → 스냅샷. 체결확인(잔고 폴링) 이후 호출되므로 '체결 후 상태'가 찍힌다.

    주간 리밸런싱 스킵 날에도 main()에서 호출돼 평일 매일 1포인트를 남긴다(#218 C3의 재조회
    회피는 매매 경로 얘기 — 이 훅은 잡당 1콜의 의도된 재조회). 조회 실패 포함 전부 내부 흡수.
    """
    try:
        bal = balance_fn()
        positions_value = sum(x["eval"] for x in bal["positions"])
        return record_snapshot(market, KIS_ACCOUNT, bal["currency"], bal["cash"] + positions_value,
                               cash=bal["cash"], positions_value=positions_value)
    except Exception as e:
        logger.error(f"{market} 잔고 조회 실패(비치명): {type(e).__name__}: {e}")
        return False
