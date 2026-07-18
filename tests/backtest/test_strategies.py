"""후보 전략 검증: 레지스트리 등록 + 엔진 구동 + 신호 방향."""
import math
import unittest
from collections import deque
from decimal import Decimal

from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.models import BTick
from trading.strategy.registry import available, get_strategy


def _series(prices):
    return [BTick("KRW-BTC", Decimal(str(p)), float(i)) for i, p in enumerate(prices)]


def _dq(prices):
    return deque((Decimal(str(p)) for p in prices), maxlen=200)


class TestRegistry(unittest.TestCase):
    def test_all_registered(self):
        self.assertEqual(set(available()),
                         {"sma", "rsi", "macd", "bollinger", "breakout", "trend", "ensemble",
                          "xs_momentum", "xs_reversal", "orb", "intraday_momentum"})

    def test_get_strategy_returns_named_instance(self):
        for name in available():
            self.assertEqual(get_strategy(name).name, name)


class TestStrategiesRunInEngine(unittest.TestCase):
    def test_each_runs_without_error(self):
        # 진동+완만 추세 혼합 → 모든 전략이 예외 없이 구동되고 봉 수가 입력과 일치
        prices = [100 + 10 * math.sin(i / 5) + i * 0.05 for i in range(400)]
        ticks = _series(prices)
        for name in ("rsi", "macd", "bollinger", "breakout"):
            with self.subTest(strategy=name):
                acct = BacktestAccount(Decimal("1000000"))
                eng = BacktestEngine(acct, FillModel(), equity_sample_sec=60.0)
                eng.run(ticks, get_strategy(name))
                self.assertEqual(eng.n_bars, len(ticks))


class TestSignalDirection(unittest.TestCase):
    def test_rsi_buys_oversold_sells_overbought(self):
        s = get_strategy("rsi")
        self.assertEqual(s._signal("KRW-BTC", _dq(range(30, 14, -1))), "BUY")   # 급락 → RSI 0
        self.assertEqual(s._signal("KRW-BTC", _dq(range(14, 30))), "SELL")      # 급등 → RSI 100

    def test_breakout_buys_new_high(self):
        s = get_strategy("breakout")
        self.assertEqual(s._signal("KRW-BTC", _dq([100] * 20 + [105])), "BUY")  # 직전고점 돌파
        self.assertEqual(s._signal("KRW-BTC", _dq([100] * 20 + [95])), "SELL")  # 직전저점 이탈

    def test_bollinger_buys_below_lower_band(self):
        s = get_strategy("bollinger")
        self.assertEqual(s._signal("KRW-BTC", _dq([100] * 19 + [90])), "BUY")   # 하단 이탈
        self.assertEqual(s._signal("KRW-BTC", _dq([100] * 19 + [110])), "SELL") # 상단 돌파

    def test_bollinger_flat_series_returns_none(self):
        # σ=0(평탄) 시 lower==mid==upper==price → price<=lower True여서 BUY 오발 방지
        s = get_strategy("bollinger")
        self.assertIsNone(s._signal("KRW-BTC", _dq([100] * 20)))

    def test_macd_emits_buy_on_upward_reversal(self):
        s = get_strategy("macd")
        prices = [100 - i for i in range(40)] + [60 + 2 * i for i in range(40)]  # 하락 후 반등
        sigs = []
        dq = deque(maxlen=2)
        for p in prices:
            dq.append(Decimal(str(p)))
            sigs.append(s._signal("KRW-BTC", dq))
        self.assertIn("BUY", sigs)


if __name__ == "__main__":
    unittest.main()
