"""볼린저밴드 전략 (단일 책임: 밴드 하단 매수·상단 매도, 평균회귀).

현재가가 하단 밴드(SMA−2σ) 이하면 매수, 상단 밴드(SMA+2σ) 이상이면 청산. STOP/TAKE/TRAIL 병행.
"""
from trading.strategy.plugins.disciplined import DisciplinedStrategy
from trading.strategy.core.indicators import bollinger

_WINDOW = 20
_K = 2.0


class BollingerStrategy(DisciplinedStrategy):
    name = "bollinger"
    window = _WINDOW

    def _signal(self, symbol, prices):
        band = bollinger(prices, _WINDOW, _K)
        if band is None:
            return None
        lower, _mid, upper = band
        if upper <= lower:      # σ=0(평탄 시계열) — lower==mid==upper==price, price<=lower가 True가 돼 오매수 방지
            return None
        price = float(prices[-1])
        if price <= lower:
            return "BUY"
        if price >= upper:
            return "SELL"
        return None
