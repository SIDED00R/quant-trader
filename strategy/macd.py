"""MACD 전략 (단일 책임: MACD선·시그널선 교차 모멘텀).

MACD선(EMA12−EMA26)이 시그널선(MACD의 EMA9)을 상향 교차하면 매수, 하향 교차하면 청산.
EMA는 경로 의존이라 종목별 증분 상태(Ema)로 유지한다. EMA 수렴 전(_SLOW봉 미만)엔 신호 억제.
"""
from strategy.disciplined import DisciplinedStrategy
from strategy.indicators import Ema

_FAST, _SLOW, _SIGNAL = 12, 26, 9


class MACDStrategy(DisciplinedStrategy):
    name = "macd"
    window = 2  # 증분 EMA 상태로 계산 → 가격 버퍼는 현재가만 필요

    def __init__(self):
        super().__init__()
        self._state: dict[str, dict] = {}  # sym -> {fast, slow, sig, prev_hist, n}

    def _signal(self, symbol, prices):
        price = prices[-1]
        st = self._state.get(symbol)
        if st is None:
            st = {"fast": Ema(_FAST), "slow": Ema(_SLOW), "sig": Ema(_SIGNAL), "prev": None, "n": 0}
            self._state[symbol] = st
        macd_line = st["fast"].update(price) - st["slow"].update(price)
        sig_line = st["sig"].update(macd_line)
        hist = macd_line - sig_line
        prev = st["prev"]
        st["prev"] = hist
        st["n"] += 1
        if st["n"] < _SLOW or prev is None:  # 수렴 전·첫 봉은 교차 판정 보류
            return None
        if prev <= 0 < hist:                 # 상향 교차
            return "BUY"
        if prev >= 0 > hist:                 # 하향 교차
            return "SELL"
        return None
