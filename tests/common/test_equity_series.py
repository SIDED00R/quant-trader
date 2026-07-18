"""자산 시계열 합성 검증 (merge_total_krw/common_start/rebase_pct/_fx_at — 순수 함수, DB/네트워크 없음).

TOTAL 합성 계약: 참여 시장 전부 관측 시작 후부터 · 결측일 forward-fill · US는 usdkrw 환산 ·
US 참여인데 환율 없으면 빈 리스트(부분 합산 착시 방지) · KRW 시장만이면 환율 없이도 합산.
리베이스 계약: 공통 시작일(=늦게 합류한 시장의 첫날) 값=0% · 기준값은 start 이전 마지막 관측(forward-fill) ·
첫 포인트는 앵커 (start, 0.0) — 모든 시리즈가 같은 시점·같은 값에서 출발.
"""
import unittest
from datetime import date

from common.equity_series import (
    PAPER_MARKETS,
    _fx_at,
    chart_rows,
    common_start,
    merge_total_krw,
    rebase_pct,
)

D = date


class TestCommonStart(unittest.TestCase):
    def test_max_of_first_dates(self):
        markets = {
            "COIN": [(D(2026, 6, 28), 10.0, None), (D(2026, 7, 2), 12.0, None)],
            "KR": [(D(2026, 7, 1), 100.0, None)],
            "US": [],                                     # 데이터 없는 시장은 무시
        }
        self.assertEqual(common_start(markets), D(2026, 7, 1))

    def test_empty_markets(self):
        self.assertIsNone(common_start({"COIN": [], "KR": [], "US": []}))


class TestRebasePct(unittest.TestCase):
    def test_anchor_zero_and_signed_pct(self):
        pts = rebase_pct([(D(2026, 7, 1), 200.0), (D(2026, 7, 2), 220.0), (D(2026, 7, 3), 190.0)],
                         D(2026, 7, 1))
        self.assertEqual(pts[0], (D(2026, 7, 1), 0.0))    # 앵커 = 시작점 0%
        self.assertAlmostEqual(pts[1][1], 10.0)           # 수익 +
        self.assertAlmostEqual(pts[2][1], -5.0)           # 손실 −

    def test_forward_fill_base_from_earlier_history(self):
        # 시작이 빠른 시장 — 공통 시작일에 정확한 관측이 없어도 직전 값(250)이 기준.
        pts = rebase_pct([(D(2026, 6, 28), 250.0), (D(2026, 7, 2), 275.0)], D(2026, 7, 1))
        self.assertEqual(pts[0], (D(2026, 7, 1), 0.0))
        self.assertAlmostEqual(pts[1][1], 10.0)           # 275/250 − 1

    def test_empty_and_zero_base(self):
        self.assertEqual(rebase_pct([], D(2026, 7, 1)), [])
        self.assertEqual(rebase_pct([(D(2026, 7, 1), 0.0)], D(2026, 7, 1)), [])

    def test_accepts_cash_column(self):
        # (date, equity, cash) 3튜플도 그대로 소화
        pts = rebase_pct([(D(2026, 7, 1), 100.0, 40.0), (D(2026, 7, 2), 150.0, 40.0)], D(2026, 7, 1))
        self.assertAlmostEqual(pts[1][1], 50.0)


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


class TestChartRowsPaper(unittest.TestCase):
    """페이퍼 시장(KR_ICHIMOKU)은 TOTAL·공통시작 제외 + 자기 시작일 앵커."""
    FX = [(D(2026, 1, 1), 1000.0), (D(2026, 6, 1), 1000.0)]
    MARKETS = {
        "COIN": [(D(2026, 1, 1), 100.0, None), (D(2026, 6, 1), 150.0, None)],
        "KR": [(D(2026, 1, 1), 100.0, None), (D(2026, 6, 1), 120.0, None)],
        "US": [(D(2026, 1, 1), 10.0, None), (D(2026, 6, 1), 11.0, None)],
        "KR_ICHIMOKU": [(D(2026, 5, 1), 1e8, None), (D(2026, 6, 1), 1.1e8, None)],  # 늦은 합류
    }

    def test_paper_marker(self):
        self.assertIn("KR_ICHIMOKU", PAPER_MARKETS)

    def test_paper_self_anchored_and_excluded_from_total(self):
        rows = {r["key"]: r for r in chart_rows(self.MARKETS, self.FX)}
        # 페이퍼는 자기 첫날(5/1)=0%에서 출발, +10% 상승
        self.assertEqual(rows["KR_ICHIMOKU"]["points"][0], (D(2026, 5, 1), 0.0))
        self.assertAlmostEqual(rows["KR_ICHIMOKU"]["ret"], 10.0)
        # TOTAL은 실운용(COIN+KR+US×fx)만 — 1억대 페이퍼가 섞이지 않음
        self.assertLess(rows["TOTAL"]["last_value"], 1e6)
        # 실운용 시리즈는 공통 시작일(1/1)에 앵커
        self.assertEqual(rows["KR"]["points"][0], (D(2026, 1, 1), 0.0))


if __name__ == "__main__":
    unittest.main()
