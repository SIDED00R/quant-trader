"""거래소 휴장일 회귀 테스트 (단일 책임: KR 2026 셋 정합성 + US 셋 회귀)."""
import unittest
from datetime import date

from common.market_holidays import _KRX, is_market_holiday


class TestKrHolidays2026(unittest.TestCase):
    def test_all_weekdays(self):
        for d in _KRX:
            self.assertLess(d.weekday(), 5, f"{d}는 주말(휴장 셋은 평일만 수록)")

    def test_election_day_included(self):
        self.assertIn(date(2026, 6, 3), _KRX)

    def test_year_end_closure_included(self):
        self.assertIn(date(2026, 12, 31), _KRX)

    def test_regular_weekday_not_holiday(self):
        self.assertFalse(is_market_holiday("KR", date(2026, 7, 6)))


class TestUsHolidaysRegression(unittest.TestCase):
    def test_independence_day_observed(self):
        self.assertTrue(is_market_holiday("US", date(2026, 7, 3)))


if __name__ == "__main__":
    unittest.main()
