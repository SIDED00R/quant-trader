"""돌파 전략 (단일 책임: 도닉언 채널 돌파 모멘텀).

현재가가 직전 lookback봉 최고가를 상향 돌파하면 매수(추세 추종), 최저가를 하향 이탈하면 청산.
평균회귀(RSI/볼린저)와 반대 방향의 모멘텀 전략. STOP/TAKE/TRAIL 병행.
"""
from strategy.disciplined import DisciplinedStrategy
from strategy.indicators import donchian

_LOOKBACK = 20


class BreakoutStrategy(DisciplinedStrategy):
    name = "breakout"
    window = _LOOKBACK + 1

    def _signal(self, symbol, prices):
        channel = donchian(prices, _LOOKBACK)
        if channel is None:
            return None
        low, high = channel
        price = float(prices[-1])
        if price > high:
            return "BUY"
        if price < low:
            return "SELL"
        return None
