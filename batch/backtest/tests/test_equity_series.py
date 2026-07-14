"""자산 시계열 합성 검증 (merge_total_krw/normalize/_fx_at — 순수 함수, DB/네트워크 없음).

TOTAL 합성 계약: 참여 시장 전부 관측 시작 후부터 · 결측일 forward-fill · US는 usdkrw 환산 ·
US 참여인데 환율 없으면 빈 리스트(부분 합산 착시 방지) · KRW 시장만이면 환율 없이도 합산.
"""
import unittest
from datetime import date

from common.equity_series import _fx_at, merge_total_krw, normalize

D = date


class TestNormalize(unittest.TestCase):
    def test_first_point_is_100(self):
        pts = normalize([(D(2026, 7, 1), 200.0), (D(2026, 7, 2), 220.0)])
        self.assertEqual(pts[0][1], 100.0)
        self.assertAlmostEqual(pts[1][1], 110.0)

    def test_empty_and_zero_base(self):
        self.assertEqual(normalize([]), [])
        self.assertEqual(normalize([(D(2026, 7, 1), 0.0)]), [])

    def test_accepts_cash_column(self):
        # (date, equity, cash) 3튜플도 그대로 소화
        pts = normalize([(D(2026, 7, 1), 100.0, 40.0), (D(2026, 7, 2), 150.0, 40.0)])
        self.assertAlmostEqual(pts[1][1], 150.0)


class TestFxAt(unittest.TestCase):
    def test_forward_fill_and_before_range(self):
        fx = [(D(2026, 7, 1), 1300.0), (D(2026, 7, 3), 1350.0)]
        dates = [d for d, _ in fx]
        self.assertEqual(_fx_at(fx, dates, D(2026, 7, 2)), 1300.0)   # 결측일 → 직전 값
        self.assertEqual(_fx_at(fx, dates, D(2026, 7, 3)), 1350.0)
        self.assertIsNone(_fx_at(fx, dates, D(2026, 6, 30)))         # 시계열 이전 → None


class TestMergeTotal(unittest.TestCase):
    FX = [(D(2026, 7, 1), 1000.0), (D(2026, 7, 2), 1100.0)]

    def test_starts_when_all_markets_present(self):
        series = {
            "COIN": [(D(2026, 6, 28), 10.0, None), (D(2026, 7, 1), 12.0, None)],
            "KR": [(D(2026, 7, 1), 100.0, None)],
            "US": [(D(2026, 7, 1), 1.0, None)],
        }
        total = merge_total_krw(series, self.FX)
        self.assertEqual(total[0][0], D(2026, 7, 1))       # 6/28(코인만) 제외
        self.assertAlmostEqual(total[0][1], 12.0 + 100.0 + 1.0 * 1000.0)

    def test_forward_fill_missing_market_day(self):
        series = {
            "COIN": [(D(2026, 7, 1), 10.0, None), (D(2026, 7, 2), 20.0, None)],
            "KR": [(D(2026, 7, 1), 100.0, None)],           # 7/2 결측 → 캐리
            "US": [(D(2026, 7, 1), 1.0, None), (D(2026, 7, 2), 2.0, None)],
        }
        total = merge_total_krw(series, self.FX)
        self.assertAlmostEqual(total[1][1], 20.0 + 100.0 + 2.0 * 1100.0)

    def test_us_without_fx_returns_empty(self):
        series = {"COIN": [(D(2026, 7, 1), 10.0, None)], "KR": [], "US": [(D(2026, 7, 1), 1.0, None)]}
        self.assertEqual(merge_total_krw(series, []), [])

    def test_krw_only_without_fx_still_sums(self):
        series = {"COIN": [(D(2026, 7, 1), 10.0, None), (D(2026, 7, 2), 11.0, None)],
                  "KR": [(D(2026, 7, 1), 100.0, None)], "US": []}
        total = merge_total_krw(series, [])
        self.assertAlmostEqual(total[0][1], 110.0)
        self.assertAlmostEqual(total[1][1], 111.0)

    def test_empty_series(self):
        self.assertEqual(merge_total_krw({"COIN": [], "KR": [], "US": []}, self.FX), [])


if __name__ == "__main__":
    unittest.main()
