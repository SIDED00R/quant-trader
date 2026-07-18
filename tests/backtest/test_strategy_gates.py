"""1.5단계 게이트 검증 (합성 데이터): 수수료 인지 진입 필터 + 데드크로스 청산 토글.

전략 가드는 sma 모듈 상수를 패치해 튜닝 기본값과 분리한다(로직만 결정적으로 검증).
"""
import unittest
from decimal import Decimal
from unittest import mock

import trading.strategy.sma as sma_mod
from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.models import BTick
from trading.strategy.sma import SMAStrategy


def _rise(n, step):
    return [BTick("KRW-BTC", Decimal("100") * (Decimal(1) + Decimal(step) * i), float(i)) for i in range(n)]


def _deadcross_series():
    """상승 31틱 → 완만 하락(-0.04%/틱) 55틱: SELL 확정+최소보유 경과로 데드크로스 자격 발생."""
    t = _rise(31, "0.0015")
    peak = t[-1].price
    t += [BTick("KRW-BTC", peak * (Decimal(1) - Decimal("0.0004") * j), float(30 + j)) for j in range(1, 56)]
    return t


def _patch(**vals):
    return [mock.patch.object(sma_mod, k, v) for k, v in vals.items()]


class TestFeeAwareFilter(unittest.TestCase):
    def setUp(self):
        for p in _patch(STRATEGY_WARMUP_SEC=25, STRATEGY_COOLDOWN_SEC=0,
                        STRATEGY_MIN_HOLD_SEC=0, STRATEGY_DEADCROSS_EXIT=False):
            p.start()
            self.addCleanup(p.stop)

    def _run(self):
        acct = BacktestAccount(Decimal("1000000"))
        eng = BacktestEngine(acct, FillModel(), equity_sample_sec=10.0)
        eng.run(_rise(40, "0.001"), SMAStrategy())  # 완만 상승(TAKE 미도달) → 진입 여부만 관찰
        return acct

    def test_enters_when_filter_below_signal(self):
        with mock.patch.object(sma_mod, "STRATEGY_MIN_EDGE_PCT", Decimal("0")):
            acct = self._run()
        self.assertGreater(acct.qty("KRW-BTC"), Decimal("0"), "필터가 신호보다 낮으면 진입해야 함")

    def test_blocked_when_filter_above_signal(self):
        with mock.patch.object(sma_mod, "STRATEGY_MIN_EDGE_PCT", Decimal("1")):  # 100% — 어떤 신호도 차단
            acct = self._run()
        self.assertEqual(acct.qty("KRW-BTC"), Decimal("0"), "필터가 신호보다 높으면 진입 차단")


class TestDeadcrossToggle(unittest.TestCase):
    def setUp(self):
        for p in _patch(STRATEGY_WARMUP_SEC=30, STRATEGY_COOLDOWN_SEC=15,
                        STRATEGY_MIN_HOLD_SEC=20, STRATEGY_MIN_EDGE_PCT=Decimal("0")):
            p.start()
            self.addCleanup(p.stop)

    def _reasons(self):
        acct = BacktestAccount(Decimal("1000000"))
        eng = BacktestEngine(acct, FillModel(), equity_sample_sec=10.0)
        eng.run(_deadcross_series(), SMAStrategy())
        return [t.reason for t in eng.closed_trades]

    def test_deadcross_exit_when_enabled(self):
        with mock.patch.object(sma_mod, "STRATEGY_DEADCROSS_EXIT", True):
            self.assertIn("DEADCROSS", self._reasons())

    def test_no_deadcross_exit_when_disabled(self):
        with mock.patch.object(sma_mod, "STRATEGY_DEADCROSS_EXIT", False):
            self.assertNotIn("DEADCROSS", self._reasons())


if __name__ == "__main__":
    unittest.main()
