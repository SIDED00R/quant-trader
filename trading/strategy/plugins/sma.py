"""SMA 전략 (단일 책임: 규율 기반 SMA 의사결정).

과거 라이브 SMA 틱봇(앙상블 경로로 대체·제거됨)의 매 틱 로직을 단일 계좌·동기 체결로
수행하는 Strategy 구현. 신호/사이징/청산 임계값은 sma_trader의 순수 함수·상수를 그대로
import해 발산을 없앤다.

1.5단계(거래빈도·수수료 제어): 봉 단위 가드(config 재튜닝) + 수수료 인지 진입 필터
(STRATEGY_MIN_EDGE_PCT) + 데드크로스 청산 토글(STRATEGY_DEADCROSS_EXIT, 기본 off)을 적용한다.
"""
from collections import deque
from decimal import Decimal

from common.config import (
    SMA_LONG,
    STRATEGY_CONFIRM_TICKS,
    STRATEGY_COOLDOWN_SEC,
    STRATEGY_DEADCROSS_EXIT,
    STRATEGY_MAX_POSITIONS,
    STRATEGY_MIN_EDGE_PCT,
    STRATEGY_MIN_HOLD_SEC,
    STRATEGY_WARMUP_SEC,
)
from trading.strategy.core.base import Broker, MarketTick, Strategy
from trading.strategy.plugins.sma_trader import (
    MIN_ORDER_KRW,
    liquidation_reason,
    position_fraction,
    sma_gap,
    sma_state,
)

_NEG_INF = -1e18  # 라이브의 -1e9 대용(시장시간 epoch는 1e9 규모라 충분히 작은 값 사용)


class SMAStrategy(Strategy):
    name = "sma"

    def __init__(self):
        self.prices: dict[str, deque] = {}
        self.state: dict[str, str] = {}      # 확정 추세(symbol -> BUY/SELL)
        self.pending: dict[str, list] = {}   # symbol -> [후보상태, 연속틱수]
        self.peak: dict[str, Decimal] = {}
        self.entry_time: dict[str, float] = {}
        self.last_exit: dict[str, float] = {}
        self.started_at: float | None = None

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        sym, price, now = tick.symbol, tick.price, tick.ts
        if self.started_at is None:
            # 워밍업 기준 시계: 라이브는 프로세스 기동 벽시계(time.monotonic), 백테스트는 replay
            # 시작 시장시간(trade_ts)을 쓴다. replay에선 시장시간이 올바른 기준이나, 데이터 공백
            # 구간에선 라이브(벽시계)와 게이트 해제 시점이 어긋날 수 있다(backtest/README.md 참조).
            self.started_at = now
        dq = self.prices.setdefault(sym, deque(maxlen=SMA_LONG))
        dq.append(price)

        # (1) 보유 포지션 매 틱 청산 우선(손절>익절>트레일링). 워밍업과 무관.
        self._check_liquidations(sym, price, now, broker)

        # (2) 확인봉으로 확정 추세 갱신
        raw = sma_state(dq)
        if raw is not None and raw != self.state.get(sym):
            pend = self.pending.get(sym)
            if pend and pend[0] == raw:
                pend[1] += 1
            else:
                pend = [raw, 1]
                self.pending[sym] = pend
            if pend[1] >= STRATEGY_CONFIRM_TICKS:
                self.state[sym] = raw
                self.pending.pop(sym, None)
        else:
            self.pending.pop(sym, None)

        # (3) 확정 추세와 현재 틱이 일치할 때만 매매 자격 재평가
        if self.state.get(sym) == "BUY" and raw == "BUY":
            self._enter(sym, price, now, sma_gap(dq), broker)
        elif STRATEGY_DEADCROSS_EXIT and self.state.get(sym) == "SELL" and raw == "SELL":
            self._exit_deadcross(sym, now, broker)

    def _check_liquidations(self, sym, price, now, broker):
        qty = broker.position_qty(sym)
        avg = broker.position_avg(sym)
        if qty <= 0 or avg <= 0:
            self.peak.pop(sym, None)
            return
        pnl = price / avg - 1
        peak = self.peak.get(sym, pnl)
        if pnl > peak:
            peak = pnl
        self.peak[sym] = peak
        reason = liquidation_reason(pnl, peak)
        if reason and broker.sell(sym, qty, reason, now):
            self.last_exit[sym] = now
            self.peak.pop(sym, None)
            self.entry_time.pop(sym, None)

    def _enter(self, sym, price, now, gap, broker):
        if now - self.started_at < STRATEGY_WARMUP_SEC:
            return
        if gap is None or abs(gap) < STRATEGY_MIN_EDGE_PCT:  # 수수료 인지 필터: 약신호 진입 차단
            return
        if price is None or price <= 0:
            return
        if broker.position_qty(sym) > 0:
            return
        if now - self.last_exit.get(sym, _NEG_INF) < STRATEGY_COOLDOWN_SEC:
            return
        if broker.open_symbol_count() >= STRATEGY_MAX_POSITIONS:
            return
        frac = position_fraction(gap)
        available = broker.cash()
        budget = available * frac if available > 0 else Decimal(0)
        if budget < MIN_ORDER_KRW:
            return
        qty = (budget / price).quantize(Decimal("0.00000001"))
        if qty <= 0:
            return
        if broker.buy(sym, qty, now):
            self.entry_time[sym] = now
            self.peak.pop(sym, None)

    def _exit_deadcross(self, sym, now, broker):
        if now - self.started_at < STRATEGY_WARMUP_SEC:
            return
        if broker.position_qty(sym) <= 0:
            return
        if now - self.entry_time.get(sym, _NEG_INF) < STRATEGY_MIN_HOLD_SEC:
            return
        if now - self.last_exit.get(sym, _NEG_INF) < STRATEGY_COOLDOWN_SEC:
            return
        if broker.sell(sym, broker.position_qty(sym), "DEADCROSS", now):
            self.last_exit[sym] = now
            self.peak.pop(sym, None)
            self.entry_time.pop(sym, None)
