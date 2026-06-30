"""다중 추세속도 앙상블 (단일 책임: 여러 TrendSignal의 목표비중 가중합 → 합성 목표로 주문).

4단계 Commander의 백테스트 구현 — 서로 다른 (short,long) 추세 부하의 목표비중을 가중평균해 종목별
합성 목표비중을 만들고, 보유 비중을 그 목표로 (밴드 초과 시) 재조정한다. 단일 속도의 파라미터 리스크·
whipsaw를 분산한다(부하 다수가 동의할수록 비중↑). config/base/trend/trend_signal에만 의존(Kafka/DB 비의존).
"""
from decimal import Decimal

from common.config import ENSEMBLE_REBALANCE_BAND
from trading.strategy.base import Broker, MarketTick, Strategy
from trading.strategy.rebalance import decide
from trading.strategy.trend_signal import TrendSignal

# 빠름/중간/느림 — 단일 5/40의 속도 편중을 분산(파라미터 리스크↓). BTC/ETH 6.6년 교차검증 채택 구성.
_DEFAULT_SPECS = [(5, 40), (10, 60), (20, 100)]


def load_name(short, long) -> str:
    """부하(전략) 식별자 — 다부하 Commander 신호 태그/가중치 키. 워커·commander 공유."""
    return f"trend-{short}-{long}"


def default_loads() -> list[tuple]:
    """채택 구성의 부하 목록 [(name, short, long), ...] (5단계 다부하 분리용)."""
    return [(load_name(s, l), s, l) for s, l in _DEFAULT_SPECS]


class EnsembleStrategy(Strategy):
    name = "ensemble"

    def __init__(self, specs=None, weights=None, rebalance_band=None):
        specs = specs or _DEFAULT_SPECS
        self.signals = [TrendSignal(short=s, long=l) for s, l in specs]
        self.weights = [float(w) for w in (weights or [1.0] * len(self.signals))]
        if len(self.weights) != len(self.signals):
            raise ValueError("weights 길이가 specs와 다릅니다")
        self._wsum = sum(self.weights)
        # 합성 목표비중 재조정 밴드(상대). 기본 0.5(교차검증 채택). 0이면 매 봉 목표 추종(거래 급증).
        self.rebalance_band = float(ENSEMBLE_REBALANCE_BAND if rebalance_band is None else rebalance_band)

    def combined_target(self, symbol: str, price) -> Decimal:
        """각 부하 신호를 갱신하고 가중평균 합성 목표비중(0=현금)을 반환. on_tick(주문)과 대시보드 API(api/routes/strategy)가 공유.

        부작용: 각 TrendSignal의 내부 상태(가격버퍼·long 래치)를 갱신한다(매 봉 1회 호출 가정).
        """
        targets = [sig.update(symbol, price) for sig in self.signals]   # 각 부하의 목표비중(0=현금)
        return sum((Decimal(str(w)) * t for w, t in zip(self.weights, targets)), Decimal(0)) \
            / Decimal(str(self._wsum))

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        combined = self.combined_target(tick.symbol, tick.price)
        self._order_to_target(tick.symbol, tick.price, combined, tick.ts, broker)

    def _order_to_target(self, sym, price, target_w: Decimal, now, broker):
        """보유 비중을 합성 목표비중으로 조정. 목표 0이면 전량 청산, 밴드 이내 드리프트는 무시(저회전).

        재조정 산술(밴드·확대/축소·수수료 양자화)은 공용 정본 rebalance.decide와 동일(commander와 동일 규칙).
        """
        if price is None or price <= 0:
            return
        qty = broker.position_qty(sym)
        if target_w <= 0:                       # 합의가 전부 현금 → 전량 청산
            if qty > 0:
                broker.sell(sym, qty, "SIGNAL", now)
            return
        order = decide(qty, price, broker.cash(), broker.equity(), float(target_w), self.rebalance_band)
        if order is None:
            return                              # 밴드 이내 또는 최소주문 미달 → 유지(저회전)
        side, quantity = order
        if side == "BUY":                       # 확대 → 차액 매수
            broker.buy(sym, quantity, now)
        else:                                   # 목표비중>0에서의 SELL = 차액 축소
            broker.sell(sym, quantity, "REBAL", now)
