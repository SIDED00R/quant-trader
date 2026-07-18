"""SMA 순수 판정 함수 (단일 책임: 신호·사이징·청산 임계 — sma.py·disciplined.py 공용).

종목별 단기/장기 SMA 이격 밴드로 추세 상태를 판정하고, 신호 강도 비례 사이징과
손절/익절/트레일링 청산 사유를 제공한다. 백테스트 전략(sma·disciplined)이 임계값
발산 없이 공유하도록 이 모듈에 둔다.

라이브 매매는 앙상블 경로(live_ensemble→commander / trade_once)가 담당한다 — 이 모듈은 순수 판정 함수만 제공.
"""
from collections import deque
from decimal import Decimal

from common.config import (
    MIN_ORDER_KRW,  # noqa: F401 — sma·disciplined가 이 모듈에서 재수입(임계값 단일 출처)
    SMA_LONG,
    SMA_SHORT,
    STRATEGY_ENTRY_BAND,
    STRATEGY_ORDER_FRACTION_MAX,
    STRATEGY_ORDER_FRACTION_MIN,
    STRATEGY_STOP_LOSS_PCT,
    STRATEGY_STRONG_GAP,
    STRATEGY_TAKE_PROFIT_PCT,
    STRATEGY_TRAIL_ARM_PCT,
    STRATEGY_TRAIL_GIVEBACK_PCT,
)

# 퍼센트 → 비율(Decimal)
_STOP = STRATEGY_STOP_LOSS_PCT / Decimal(100)
_TAKE = STRATEGY_TAKE_PROFIT_PCT / Decimal(100)
_ARM = STRATEGY_TRAIL_ARM_PCT / Decimal(100)
_GIVEBACK = STRATEGY_TRAIL_GIVEBACK_PCT / Decimal(100)


def sma_gap(prices: deque) -> Decimal | None:
    """단기/장기 이평선의 부호있는 간격 (short-long)/long. 데이터 부족/비정상이면 None."""
    if len(prices) < SMA_LONG:
        return None
    p = list(prices)
    short = sum(p[-SMA_SHORT:]) / Decimal(SMA_SHORT)
    long_ = sum(p[-SMA_LONG:]) / Decimal(SMA_LONG)
    if long_ <= 0:
        return None
    return (short - long_) / long_


def sma_state(prices: deque) -> str | None:
    """이격 밴드 기반 추세 상태: 'BUY' | 'SELL' | None(NEUTRAL/데이터부족)."""
    gap = sma_gap(prices)
    if gap is None:
        return None
    if gap >= STRATEGY_ENTRY_BAND:
        return "BUY"
    if gap <= -STRATEGY_ENTRY_BAND:
        return "SELL"
    return None


def position_fraction(gap: Decimal) -> Decimal:
    """신호 강도(이평선 간격)에 비례한 매수 비율: 약하면 MIN, 강하면 MAX (그 사이 선형)."""
    span = STRATEGY_STRONG_GAP - STRATEGY_ENTRY_BAND
    if span <= 0:
        return STRATEGY_ORDER_FRACTION_MAX
    t = (abs(gap) - STRATEGY_ENTRY_BAND) / span
    t = max(Decimal(0), min(Decimal(1), t))
    return STRATEGY_ORDER_FRACTION_MIN + (STRATEGY_ORDER_FRACTION_MAX - STRATEGY_ORDER_FRACTION_MIN) * t


def liquidation_reason(pnl: Decimal, peak: Decimal) -> str | None:
    """청산 사유(우선순위): 손절 > 익절 > 트레일링. 없으면 None."""
    if pnl <= -_STOP:
        return "STOP"
    if pnl >= _TAKE:
        return "TAKE"
    if peak >= _ARM and pnl <= peak - _GIVEBACK:
        return "TRAIL"
    return None


def check_liquidations(st, sym, price, now, broker) -> None:
    """보유분 청산 검사·실행(손절/익절/트레일링) — 전략 인스턴스 st의 peak/last_exit/entry_time을 갱신.

    SMAStrategy·DisciplinedStrategy가 공유하는 상태 머신(동일 구현 2벌 통합). 청산 판정은 liquidation_reason.
    """
    qty = broker.position_qty(sym)
    avg = broker.position_avg(sym)
    if qty <= 0 or avg <= 0:
        st.peak.pop(sym, None)
        return
    pnl = price / avg - 1
    peak = st.peak.get(sym, pnl)
    if pnl > peak:
        peak = pnl
    st.peak[sym] = peak
    reason = liquidation_reason(pnl, peak)
    if reason and broker.sell(sym, qty, reason, now):
        st.last_exit[sym] = now
        st.peak.pop(sym, None)
        st.entry_time.pop(sym, None)
