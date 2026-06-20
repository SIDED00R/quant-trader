"""다중 추세속도 앙상블 (단일 책임: 여러 TrendSignal의 목표비중 가중합 → 합성 목표로 주문).

4단계 Commander의 백테스트 구현 — 서로 다른 (short,long) 추세 부하의 목표비중을 가중평균해 종목별
합성 목표비중을 만들고, 보유 비중을 그 목표로 (밴드 초과 시) 재조정한다. 단일 속도의 파라미터 리스크·
whipsaw를 분산한다(부하 다수가 동의할수록 비중↑). config/base/trend/trend_signal에만 의존(Kafka/DB 비의존).
"""
from decimal import Decimal

from common.config import MIN_ORDER_KRW, TREND_REBALANCE_BAND
from strategy.base import Broker, MarketTick, Strategy
from strategy.trend import affordable_qty
from strategy.trend_signal import TrendSignal

# 빠름/중간/느림 — 단일 5/40의 속도 편중을 분산(파라미터 리스크↓)
_DEFAULT_SPECS = [(5, 40), (10, 60), (20, 100)]


class EnsembleStrategy(Strategy):
    name = "ensemble"

    def __init__(self, specs=None, weights=None, rebalance_band=None):
        specs = specs or _DEFAULT_SPECS
        self.signals = [TrendSignal(short=s, long=l) for s, l in specs]
        self.weights = [float(w) for w in (weights or [1.0] * len(self.signals))]
        if len(self.weights) != len(self.signals):
            raise ValueError("weights 길이가 specs와 다릅니다")
        self._wsum = sum(self.weights)
        # 합성 목표비중 재조정 밴드(상대). 0이면 매 봉 목표 추종(앙상블은 신호가 연속적이라 기본 재조정 권장).
        self.rebalance_band = float(TREND_REBALANCE_BAND if rebalance_band is None else rebalance_band)

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        sym, price, now = tick.symbol, tick.price, tick.ts
        targets = [sig.update(sym, price) for sig in self.signals]   # 각 부하의 목표비중(0=현금)
        combined = sum((Decimal(str(w)) * t for w, t in zip(self.weights, targets)), Decimal(0)) \
            / Decimal(str(self._wsum))
        self._order_to_target(sym, price, combined, now, broker)

    def _order_to_target(self, sym, price, target_w: Decimal, now, broker):
        """보유 비중을 합성 목표비중으로 조정. 목표 0이면 전량 청산, 밴드 이내 드리프트는 무시(저회전)."""
        if price is None or price <= 0:
            return
        qty = broker.position_qty(sym)
        if target_w <= 0:                       # 합의가 전부 현금 → 전량 청산
            if qty > 0:
                broker.sell(sym, qty, "SIGNAL", now)
            return
        equity = broker.equity()
        if equity <= 0:
            return
        cur_val = qty * price
        target_val = equity * target_w
        drift = abs(cur_val - target_val) / equity
        if qty > 0 and drift < Decimal(str(self.rebalance_band)) * target_w:
            return                              # 밴드 이내 → 유지(저회전)
        if target_val > cur_val:                # 확대 → 차액 매수(현금 한도 내, 최소주문 충족 시)
            budget = min(target_val - cur_val, broker.cash())
            if budget >= MIN_ORDER_KRW:
                add = affordable_qty(budget, price)
                if add > 0:
                    broker.buy(sym, add, now)
        else:                                   # 축소 → 차액만큼 매도
            sell_qty = ((cur_val - target_val) / price).quantize(Decimal("0.00000001"))
            if sell_qty > 0:
                broker.sell(sym, min(sell_qty, qty), "REBAL", now)
