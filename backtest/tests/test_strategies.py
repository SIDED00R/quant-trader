"""후보 전략 검증: 레지스트리 등록 + 엔진 구동 + 신호 방향."""
import math
import unittest
from collections import deque
from decimal import Decimal
from unittest import mock

import strategy.disciplined as disc_mod
from backtest.account import BacktestAccount
from backtest.engine import BacktestEngine
from backtest.fills import FillModel
from backtest.models import BTick
from strategy.registry import available, get_strategy


def _series(prices):
    return [BTick("KRW-BTC", Decimal(str(p)), float(i)) for i, p in enumerate(prices)]


def _dq(prices):
    return deque((Decimal(str(p)) for p in prices), maxlen=200)


class TestRegistry(unittest.TestCase):
    def test_all_registered(self):
        self.assertEqual(set(available()), {"sma", "rsi", "macd", "bollinger", "breakout"})

    def test_get_strategy_returns_named_instance(self):
        for name in available():
            self.assertEqual(get_strategy(name).name, name)


class TestStrategiesRunInEngine(unittest.TestCase):
    def setUp(self):
        # 가드를 작게 패치해 합성 구간에서 진입/청산 경로가 실제로 실행되도록(스모크가 no-op이 되지 않게).
        # 기본 WARMUP=1500s는 짧은 합성 시계열에선 절대 경과하지 않아 매매가 한 건도 안 일어남.
        for attr, val in (("STRATEGY_WARMUP_SEC", 30), ("STRATEGY_COOLDOWN_SEC", 0),
                          ("STRATEGY_MIN_HOLD_SEC", 0)):
            p = mock.patch.object(disc_mod, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_each_runs_and_executes_trades(self):
        # 변동성 큰 진동 → 각 전략이 예외 없이 구동되고, 패치된 가드에서 실제 매매(진입→청산)가 실행됨
        prices = [100 + 12 * math.sin(i / 6) for i in range(600)]
        ticks = _series(prices)
        total_trades = 0
        for name in ("rsi", "macd", "bollinger", "breakout"):
            with self.subTest(strategy=name):
                acct = BacktestAccount(Decimal("1000000"))
                eng = BacktestEngine(acct, FillModel(), equity_sample_sec=60.0)
                eng.run(ticks, get_strategy(name))
                self.assertEqual(eng.n_bars, len(ticks))
            total_trades += len(eng.closed_trades)
        self.assertGreater(total_trades, 0, "패치된 가드에서 최소 일부 전략은 매매를 실행해야 함")


class TestSignalExit(unittest.TestCase):
    """DisciplinedStrategy._exit_signal(reason='SIGNAL') 청산 경로를 엔진으로 구동 검증."""
    def setUp(self):
        for attr, val in (("STRATEGY_WARMUP_SEC", 10), ("STRATEGY_COOLDOWN_SEC", 0),
                          ("STRATEGY_MIN_HOLD_SEC", 0)):
            p = mock.patch.object(disc_mod, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_rsi_signal_exit_reason(self):
        # 15봉 하락(바닥에서 과매도→매수) → 완만 상승(과매수→신호 청산). 상승폭이 +2% TAKE 미만이라
        # STOP/TAKE/TRAIL이 아니라 RSI 과매수 신호(SIGNAL)로 청산됨을 고정.
        falling = [100 * (1 - 0.003) ** i for i in range(15)]
        base = falling[-1]
        rising = [base * (1 + 0.0005) ** (j + 1) for j in range(22)]
        ticks = _series(falling + rising)
        acct = BacktestAccount(Decimal("1000000"))
        eng = BacktestEngine(acct, FillModel(), equity_sample_sec=60.0)
        eng.run(ticks, get_strategy("rsi"))
        self.assertIn("SIGNAL", [t.reason for t in eng.closed_trades])


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
