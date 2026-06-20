"""Walk-forward 러너 검증: fold 경계 생성 + 합성데이터 end-to-end."""
import math
import unittest
from decimal import Decimal

from backtest.fills import FillModel
from backtest.models import BTick
from backtest.walkforward import _combos, _evaluate, _folds, _sharpe_from_rets, run_walkforward
from strategy.trend import TrendStrategy

_DAY = 86400.0


class TestFolds(unittest.TestCase):
    def test_rolling_boundaries(self):
        # t0=0, 데이터 600일, prime 80, IS 180, OOS 90, step 90
        folds = _folds(0, 600 * _DAY, 80 * _DAY, 180 * _DAY, 90 * _DAY, 90 * _DAY)
        self.assertTrue(len(folds) >= 2)
        for is_prime, is_start, oos_prime, oos_start, oos_end in folds:
            self.assertLess(is_prime, is_start)        # prime이 IS 앞
            self.assertLessEqual(is_start, oos_prime)  # IS가 OOS prime 포함
            self.assertLess(oos_start, oos_end)
            self.assertAlmostEqual(oos_end - oos_start, 90 * _DAY)
            self.assertGreaterEqual(is_prime, 0)       # 데이터 시작 이전 참조 없음

    def test_too_short_no_folds(self):
        self.assertEqual(_folds(0, 100 * _DAY, 80 * _DAY, 180 * _DAY, 90 * _DAY, 90 * _DAY), [])


class TestCombos(unittest.TestCase):
    def test_all_short_lt_long(self):
        for s, l in _combos():
            self.assertLess(s, l)
        self.assertGreater(len(_combos()), 1)


class TestSharpeFromRets(unittest.TestCase):
    def test_flat_is_zero(self):
        self.assertEqual(_sharpe_from_rets([0.0, 0.0, 0.0]), 0.0)

    def test_annualization_scales(self):
        rets = [0.01, -0.005, 0.012, 0.0, 0.008]
        self.assertAlmostEqual(_sharpe_from_rets(rets, 4.0), _sharpe_from_rets(rets, 1.0) * 2.0)


class TestEndToEnd(unittest.TestCase):
    def test_runs_and_reports(self):
        # 600일 합성 일봉(완만 추세 + 진동) — 크래시 없이 fold/집계/DSR 산출
        bars = [
            BTick("KRW-BTC", Decimal(str(100 + i * 0.1 + 5 * math.sin(i / 15))), float(i) * _DAY)
            for i in range(600)
        ]
        result = run_walkforward(bars, Decimal("1000000"), FillModel(), _DAY,
                                 180 * _DAY, 90 * _DAY, 90 * _DAY, log=lambda *a: None)
        self.assertNotIn("error", result)
        self.assertGreaterEqual(result["aggregate"]["oos_folds"], 2)
        dsr = result["aggregate"]["deflated_sharpe"]
        self.assertTrue(dsr is None or 0.0 <= dsr <= 1.0)
        self.assertEqual(result["aggregate"]["n_trials"], len(_combos()))

    def test_empty_bars_error(self):
        result = run_walkforward([], Decimal("1000000"), FillModel(), _DAY,
                                 180 * _DAY, 90 * _DAY, 90 * _DAY, log=lambda *a: None)
        self.assertIn("error", result)

    def test_ensemble_strategy_path(self):
        # --strategy ensemble 경로: 고정 구성이라 IS 선택 없이 OOS 평가, n_trials=1(PSR)
        bars = [
            BTick("KRW-BTC", Decimal(str(100 + i * 0.1 + 5 * math.sin(i / 15))), float(i) * _DAY)
            for i in range(700)
        ]
        result = run_walkforward(bars, Decimal("1000000"), FillModel(), _DAY,
                                 180 * _DAY, 90 * _DAY, 90 * _DAY, strategy="ensemble", log=lambda *a: None)
        self.assertNotIn("error", result)
        self.assertEqual(result["strategy"], "ensemble")
        self.assertEqual(result["aggregate"]["n_trials"], 1)
        self.assertGreaterEqual(result["aggregate"]["oos_folds"], 2)
        for fr in result["folds"]:
            self.assertIsNone(fr["params"])   # 앙상블은 IS 그리드 선택 없음


class TestPrimeNoLeak(unittest.TestCase):
    def test_prime_region_produces_no_trades_and_oos_base_is_initial(self):
        # prime 구간에 강한 상승(매수 유인) → OOS 진입 전. NullBroker priming이므로 prime 거래 0이어야 하고,
        # OOS는 새 계좌(initial)로만 평가되어 prime 손익이 OOS에 새지 않아야 한다.
        prime_days, oos_days = 60, 40
        rise = [100 + i for i in range(prime_days)]          # prime: 급상승
        flat = [100 + prime_days] * oos_days                 # OOS: 완전 평탄 → 진입해도 수익 0 부근
        prices = rise + flat
        bars = [BTick("KRW-BTC", Decimal(str(p)), float(i) * _DAY) for i, p in enumerate(prices)]
        initial = Decimal("1000000")
        r = _evaluate(bars, lambda: TrendStrategy(short=5, long=30, vol_lookback=20),
                      0.0, prime_days * _DAY, len(prices) * _DAY, initial, FillModel(), _DAY)
        # prime 매수가 OOS로 샜다면 OOS 평탄구간에서 큰 평가손익이 잡혀 |ret|가 커진다 → 누출 없으면 0 근방
        self.assertLess(abs(r["return"]), 0.02, "prime 손익이 OOS에 누출됨")


if __name__ == "__main__":
    unittest.main()
