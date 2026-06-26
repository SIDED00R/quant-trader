"""전략 상태 라우트 (단일 책임: 앙상블 현재 스탠스 조회).

candles_1d(일봉)로 검증된 앙상블의 **현재 목표비중·추세상태**를 종목별로 산출해 대시보드에 노출한다.
신호 워커(strategy.live_ensemble)와 동일한 ensemble.combined_target 로직을 쓰되, 워커 가동과 무관하게
요청 시점에 일봉 히스토리로 직접 계산한다(일봉 전략이라 자주 안 바뀜 → 대시보드는 저빈도 폴링).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from common.config import ENSEMBLE_SYMBOLS
from trading.strategy.ensemble import EnsembleStrategy

router = APIRouter(prefix="/strategy")


@router.get("/ensemble")
def ensemble_state():
    """채택 앙상블의 종목별 현재 스탠스: 목표비중(0~1)·LONG/CASH·속도별 추세상태."""
    from common.candles import daily_candles   # CH 의존을 라우트 호출 시점으로 지연(backtest 비의존)
    out = []
    try:
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
    except Exception as e:   # ClickHouse 미가동 등 → 503(대시보드는 패널을 '데이터 없음'으로 표시)
        raise HTTPException(503, f"ensemble state unavailable: {e}")
    return out
