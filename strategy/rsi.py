"""RSI 전략 (단일 책임: RSI 과매도 매수·과매수 매도, 평균회귀).

RSI가 과매도(≤30)면 반등 기대 매수, 과매수(≥70)면 청산. 청산은 신호 외 STOP/TAKE/TRAIL도 적용.
"""
from strategy.disciplined import DisciplinedStrategy
from strategy.indicators import rsi

_PERIOD = 14
_OVERSOLD = 30.0
_OVERBOUGHT = 70.0


class RSIStrategy(DisciplinedStrategy):
    name = "rsi"
    window = _PERIOD + 1

    def _signal(self, symbol, prices):
        r = rsi(prices, _PERIOD)
        if r is None:
            return None
        if r <= _OVERSOLD:
            return "BUY"
        if r >= _OVERBOUGHT:
            return "SELL"
        return None
