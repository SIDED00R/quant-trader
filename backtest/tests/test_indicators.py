"""기술 지표 순수 함수 검증."""
import unittest

from strategy.indicators import Ema, bollinger, donchian, rsi


class TestRSI(unittest.TestCase):
    def test_all_up_is_100(self):
        self.assertEqual(rsi(list(range(1, 16)), 14), 100.0)

    def test_all_down_is_0(self):
        self.assertEqual(rsi(list(range(15, 0, -1)), 14), 0.0)

    def test_insufficient_returns_none(self):
        self.assertIsNone(rsi([1, 2, 3], 14))

    def test_alternating_is_midrange(self):
        r = rsi([10, 11] * 8, 14)  # 16개, 등락 반복
        self.assertTrue(0.0 < r < 100.0)


class TestBollinger(unittest.TestCase):
    def test_flat_series_zero_width(self):
        self.assertEqual(bollinger([100] * 20, 20, 2.0), (100.0, 100.0, 100.0))

    def test_insufficient_returns_none(self):
        self.assertIsNone(bollinger([1, 2, 3], 20))

    def test_bands_symmetric_around_mid(self):
        lower, mid, upper = bollinger(list(range(1, 21)), 20, 2.0)
        self.assertAlmostEqual(mid, 10.5)
        self.assertAlmostEqual(upper - mid, mid - lower)


class TestDonchian(unittest.TestCase):
    def test_prior_min_max_excludes_current(self):
        # lookback=5, 현재(8)는 제외하고 직전 5개[5,3,9,7,4]의 (min,max)
        self.assertEqual(donchian([5, 3, 9, 7, 4, 8], 5), (3.0, 9.0))

    def test_insufficient_returns_none(self):
        self.assertIsNone(donchian([1, 2, 3], 5))


class TestEma(unittest.TestCase):
    def test_first_update_is_seed(self):
        self.assertEqual(Ema(10).update(42), 42.0)

    def test_converges_to_constant(self):
        e = Ema(3)
        v = None
        for _ in range(50):
            v = e.update(10)
        self.assertAlmostEqual(v, 10.0)


if __name__ == "__main__":
    unittest.main()
