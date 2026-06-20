"""전략 부하 가중치 조회 (단일 책임: strategy_weights 읽기 + 동일가중 폴백).

ENSEMBLE_ADAPTIVE=False(기본)면 **항상 동일가중**(현 동작 보존). True면 strategy_weights 테이블 값 사용.
미설정 부하/합 0 등 비정상은 동일가중으로 안전 폴백한다(열화된 가중치로 거래 멈춤 방지).
"""
from common.config import ENSEMBLE_ADAPTIVE
from common.postgres_client import pool


def load_weights(load_names) -> dict:
    """{name: weight} 반환. 적응 off거나 테이블 비면 동일가중(1.0)."""
    names = list(load_names)
    equal = {n: 1.0 for n in names}
    if not ENSEMBLE_ADAPTIVE:
        return equal
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT strategy, weight FROM strategy_weights WHERE strategy = ANY(%s)", (names,)
        ).fetchall()
    out = {n: float(dict(rows).get(n, 0.0)) for n in names}
    # 일부 부하 미등록 / 합 0 → 안전 동일가중(부분 가중치로 일부 부하가 합성서 누락되는 것 방지)
    if len(rows) < len(names) or sum(out.values()) <= 0:
        return equal
    return out
