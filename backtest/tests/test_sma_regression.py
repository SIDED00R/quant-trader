"""SMAStrategy 추출 회귀(동일성) + 청산 4경로 가드.

원본(TAKE+TRAIL) 골든은 추출 *직전* 코드(backtest/strategy.py:SmaBaselineStrategy)로 캡처한 값 →
SMAStrategy가 이를 정확히 재현하면 추출이 무행동 변경임이 증명된다.
STOP/DEADCROSS 골든은 AST 동일성이 증명된 SMAStrategy로 캡처(추출 전과 등가)해, on_tick이 위임하는
청산 4경로(STOP>TAKE>TRAIL>DEADCROSS)를 모두 회귀로 고정한다(향후 재리팩터 가드 완성).
각 시계열은 결정적이며 `git show main:backtest/strategy.py`로 동일 재현 가능.
"""
import unittest
from decimal import Decimal
from unittest import mock

import strategy.sma as sma_mod
from backtest.account import BacktestAccount
from backtest.engine import BacktestEngine
from backtest.fills import FillModel
from backtest.models import BTick
from strategy.sma import SMAStrategy


def _rise(n, step="0.0015"):
    return [BTick("KRW-BTC", Decimal("100") * (Decimal(1) + Decimal(step) * i), float(i)) for i in range(n)]


def _take_trail_series():
    """상승 71틱 → 급락 40틱: 익절(TAKE) 후 트레일링(TRAIL)."""
    t = _rise(71)
    peak = t[-1].price
    t += [BTick("KRW-BTC", peak * (Decimal(1) - Decimal("0.004") * j), float(70 + j)) for j in range(1, 41)]
    return t


def _stop_series():
    """상승 후 즉시 급락(-0.6%/틱): 트레일 무장 전 -1.2% 손절(STOP)."""
    t = _rise(31)
    peak = t[-1].price
    t += [BTick("KRW-BTC", peak * (Decimal(1) - Decimal("0.006") * j), float(30 + j)) for j in range(1, 26)]
    return t


def _deadcross_series():
    """상승 후 완만한 하락(-0.04%/틱): 손절 전 SELL 확정+최소보유 경과로 데드크로스(DEADCROSS)."""
    t = _rise(31)
    peak = t[-1].price
    t += [BTick("KRW-BTC", peak * (Decimal(1) - Decimal("0.0004") * j), float(30 + j)) for j in range(1, 56)]
    return t


# (시계열, [(reason, entry_price, exit_price, pnl, entry_ts, exit_ts)...], final_equity, mdd)
CASES = [
    (_take_trail_series, [
        ("TAKE", "104.5000", "106.7500", "3681.224819892560", 30.0, 45.0),
        ("TRAIL", "109.0000", "109.6160000", "808.366660725390000", 60.0, 72.0),
    ], "1004489.5058", "0.001488842005526314358375954271"),
    (_stop_series, [
        ("STOP", "104.5000", "103.2460000", "-2330.905373848240000", 30.0, 32.0),
    ], "997669.0088", "0.0023309912"),
    (_deadcross_series, [
        ("DEADCROSS", "104.5000", "103.66400000", "-1613.7048398346400000", 30.0, 50.0),
    ], "998386.2093", "0.0016137907"),
]


class TestSmaRegression(unittest.TestCase):
    def setUp(self):
        # 골든은 1.5단계 가드 재튜닝 이전 조건에서 캡처됨 → 청산 4경로 로직 회귀를 튜닝 기본값과
        # 분리해 고정한다(워밍업/쿨다운/최소보유 짧게, 수수료필터 off, 데드크로스 on).
        for attr, val in (("STRATEGY_WARMUP_SEC", 30),
                          ("STRATEGY_COOLDOWN_SEC", 15),
                          ("STRATEGY_MIN_HOLD_SEC", 20),
                          ("STRATEGY_MIN_EDGE_PCT", Decimal("0")),
                          ("STRATEGY_DEADCROSS_EXIT", True)):
            p = mock.patch.object(sma_mod, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_liquidation_paths_match_golden(self):
        for build, golden, final_eq, mdd in CASES:
            with self.subTest(reason=golden[0][0]):
                acct = BacktestAccount(Decimal("1000000"))
                eng = BacktestEngine(acct, FillModel(), equity_sample_sec=10.0)
                eng.run(build(), SMAStrategy())

                self.assertEqual(len(eng.closed_trades), len(golden), "거래 수가 골든과 동일해야 함")
                for tr, (reason, entry, exit_, pnl, ets, xts) in zip(eng.closed_trades, golden):
                    self.assertEqual(tr.reason, reason)
                    self.assertEqual(tr.entry_price, Decimal(entry))
                    self.assertEqual(tr.exit_price, Decimal(exit_))
                    self.assertEqual(tr.pnl, Decimal(pnl))
                    self.assertEqual(tr.entry_ts, ets)
                    self.assertEqual(tr.exit_ts, xts)
                self.assertEqual(eng.final_equity, Decimal(final_eq))
                self.assertEqual(eng.max_drawdown, Decimal(mdd))

    def test_all_four_liquidation_reasons_covered(self):
        reasons = {g[0] for _, golden, _, _ in CASES for g in golden}
        self.assertEqual(reasons, {"TAKE", "TRAIL", "STOP", "DEADCROSS"})


if __name__ == "__main__":
    unittest.main()
