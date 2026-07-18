"""지표 전략 공통 규율 베이스 (단일 책임: 진입/청산 규율 + 사이징; 신호는 서브클래스).

서브클래스는 `name`, `window`(가격 버퍼 길이), `_signal(symbol, prices) -> 'BUY'|'SELL'|None`만 정의한다.
청산은 자본보호(STOP/TAKE/TRAIL, sma_trader 재사용) + 신호 SELL(전략 고유, reason='SIGNAL')로 이뤄진다.
가드(워밍업·쿨다운·최소보유·최대보유)와 최소주문액은 SMAStrategy와 동일 config를 공유한다.
사이징은 고정 비중(order_fraction) — 신호가 이산이라 SMA의 강도 비례 대신 단순 고정.
"""
from abc import abstractmethod
from collections import deque
from decimal import Decimal

from common.config import (
    STRATEGY_COOLDOWN_SEC,
    STRATEGY_MAX_POSITIONS,
    STRATEGY_MIN_HOLD_SEC,
    STRATEGY_ORDER_FRACTION_MAX,
    STRATEGY_WARMUP_SEC,
)
from trading.strategy.core.base import Broker, MarketTick, Strategy
from trading.strategy.plugins.sma_trader import MIN_ORDER_KRW, liquidation_reason

_NEG_INF = -1e18  # 미설정 시각의 하한(쿨다운/최소보유 비교에서 항상 경과로 취급)


class DisciplinedStrategy(Strategy):
    name = "disciplined"
    window = 60                                   # 가격 버퍼 길이(서브클래스가 지표에 맞게 오버라이드)
    order_fraction = STRATEGY_ORDER_FRACTION_MAX  # 1회 매수 비중(현금 대비)

    def __init__(self):
        self.prices: dict[str, deque] = {}
        self.peak: dict[str, Decimal] = {}        # 보유 중 최고 미실현 수익률(트레일링용)
        self.entry_time: dict[str, float] = {}
        self.last_exit: dict[str, float] = {}
        self.started_at: float | None = None

    @abstractmethod
    def _signal(self, symbol: str, prices: deque) -> str | None:
        """가격 버퍼로 'BUY'|'SELL'|None 판정(데이터 부족이면 None)."""

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        sym, price, now = tick.symbol, tick.price, tick.ts
        if self.started_at is None:
            self.started_at = now
        dq = self.prices.setdefault(sym, deque(maxlen=self.window))
        dq.append(price)
        self._check_liquidations(sym, price, now, broker)  # 자본보호 우선(워밍업 무관)
        sig = self._signal(sym, dq)
        if sig == "BUY":
            self._enter(sym, price, now, broker)
        elif sig == "SELL":
            self._exit_signal(sym, now, broker)

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

    def _enter(self, sym, price, now, broker):
        if now - self.started_at < STRATEGY_WARMUP_SEC:
            return
        if price is None or price <= 0:
            return
        if broker.position_qty(sym) > 0:
            return
        if now - self.last_exit.get(sym, _NEG_INF) < STRATEGY_COOLDOWN_SEC:
            return
        if broker.open_symbol_count() >= STRATEGY_MAX_POSITIONS:
            return
        available = broker.cash()
        budget = available * self.order_fraction if available > 0 else Decimal(0)
        if budget < MIN_ORDER_KRW:
            return
        qty = (budget / price).quantize(Decimal("0.00000001"))
        if qty <= 0:
            return
        if broker.buy(sym, qty, now):
            self.entry_time[sym] = now
            self.peak.pop(sym, None)

    def _exit_signal(self, sym, now, broker):
        if now - self.started_at < STRATEGY_WARMUP_SEC:
            return
        if broker.position_qty(sym) <= 0:
            return
        if now - self.entry_time.get(sym, _NEG_INF) < STRATEGY_MIN_HOLD_SEC:
            return
        if now - self.last_exit.get(sym, _NEG_INF) < STRATEGY_COOLDOWN_SEC:
            return
        if broker.sell(sym, broker.position_qty(sym), "SIGNAL", now):
            self.last_exit[sym] = now
            self.peak.pop(sym, None)
            self.entry_time.pop(sym, None)
