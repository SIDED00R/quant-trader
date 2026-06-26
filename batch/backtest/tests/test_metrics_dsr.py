"""Deflated/Probabilistic Sharpe 검증 (Bailey & López de Prado 2014)."""
import statistics
import unittest

from batch.backtest.metrics import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe


class TestProbabilisticSharpe(unittest.TestCase):
    def test_zero_excess_is_half(self):
        # sr == benchmark → z=0 → PSR=0.5
        self.assertAlmostEqual(probabilistic_sharpe(0.1, 250, 0.0, 3.0, benchmark=0.1), 0.5, places=6)

    def test_higher_sr_higher_psr(self):
        lo = probabilistic_sharpe(0.05, 250, 0.0, 3.0)
        hi = probabilistic_sharpe(0.15, 250, 0.0, 3.0)
        self.assertLess(lo, hi)
        self.assertTrue(0.0 < lo < 1.0 and 0.0 < hi < 1.0)

    def test_insufficient_samples_none(self):
        self.assertIsNone(probabilistic_sharpe(0.1, 1, 0.0, 3.0))


class TestExpectedMaxSharpe(unittest.TestCase):
    def test_known_value(self):
        # √0.04·[(1-γ)Z(1-1/9)+γZ(1-1/(9e))] ≈ 0.304 (손계산 검증)
        self.assertAlmostEqual(expected_max_sharpe(0.04, 9), 0.3042, places=3)

    def test_more_trials_raises_bar(self):
        self.assertLess(expected_max_sharpe(0.04, 5), expected_max_sharpe(0.04, 50))

    def test_single_trial_or_zero_var_is_zero(self):
        self.assertEqual(expected_max_sharpe(0.04, 1), 0.0)
        self.assertEqual(expected_max_sharpe(0.0, 9), 0.0)


class TestDeflatedSharpe(unittest.TestCase):
    def test_deflation_lowers_significance(self):
        # 동일 수익률에서 시도 N↑ → 기준선↑ → DSR↓
        rets = [0.01, -0.004, 0.012, 0.0, 0.009, -0.006, 0.007] * 40
        few = deflated_sharpe(rets, 0.02, 2)
        many = deflated_sharpe(rets, 0.02, 50)
        self.assertGreater(few, many)

    def test_insufficient_returns_none(self):
        self.assertIsNone(deflated_sharpe([0.01], 0.02, 9))

    def test_flat_returns_none(self):
        self.assertIsNone(deflated_sharpe([0.0] * 50, 0.02, 9))


if __name__ == "__main__":
    unittest.main()
