"""전략 상태 라우트 (단일 책임: 앙상블 현재 스탠스 조회).

candles_1d(일봉)로 검증된 앙상블의 **현재 목표비중·추세상태**를 종목별로 산출해 대시보드에 노출한다.
신호 워커(strategy.live_ensemble)와 동일한 ensemble.combined_target 로직을 쓰되, 워커 가동과 무관하게
일봉 히스토리로 직접 계산한다. 일봉 전략이라 하루 안에 안 바뀜 → **TTL 1h 캐시**(api.cache, 웜업 선계산)
— 전체 히스토리 재계산(수 초)을 요청 경로에서 제거. 계산 실패 시 stale 캐시 폴백.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from api.cache import cache_get, cache_get_stale, cache_set
from common.config import ENSEMBLE_SYMBOLS
from trading.strategy.plugins.ensemble import EnsembleStrategy

router = APIRouter(prefix="/strategy")

CACHE_KEY = "strategy_ensemble"
_TTL_SEC = 3600


def compute_ensemble() -> list:
    """종목별 앙상블 스탠스 전체 계산(전체 일봉 히스토리 필요 — 느림, 캐시 뒤에서만 호출)."""
    from common.marketdata.candles import daily_candles   # CH 의존을 호출 시점으로 지연(backtest 비의존)
    out = []
    for sym in ENSEMBLE_SYMBOLS:
        ens = EnsembleStrategy()
        combined = 0.0
        last_day = None
        bars = 0
        for _sym, close, ts in daily_candles([sym]):
            combined = float(ens.combined_target(sym, close))
            last_day = ts
            bars += 1
        speeds = [
            {"short": s.short, "long": s.long,
             "state": "LONG" if s.long_state.get(sym) else "CASH"}
            for s in ens.signals
        ]
        out.append({
            "symbol": sym,
            "target_weight": combined,                 # 0~max_weight(=1.0)
            "trend_state": "LONG" if combined > 0 else "CASH",
            "max_weight": float(ens.signals[0].max_weight),
            "bar_ts": (None if last_day is None else
                       datetime.fromtimestamp(last_day, timezone.utc).date().isoformat()),
            "bars": bars,
            "speeds": speeds,
        })
    return out


@router.get("/ensemble")
def ensemble_state():
    """채택 앙상블의 종목별 현재 스탠스: 목표비중(0~1)·LONG/CASH·속도별 추세상태."""
    cached = cache_get(CACHE_KEY, _TTL_SEC)
    if cached is not None:
        return cached
    try:
        out = compute_ensemble()
    except Exception as e:   # ClickHouse 미가동 등 → stale 폴백, 그것도 없으면 503
        stale = cache_get_stale(CACHE_KEY)
        if stale is not None:
            return stale
        raise HTTPException(503, f"ensemble state unavailable: {e}")
    cache_set(CACHE_KEY, out)
    return out
