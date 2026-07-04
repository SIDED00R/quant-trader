"""주간 리밸런싱 멱등 게이트 검증 (순수 함수 — DB 불필요).

ISO 주차 키(평일 동일·다음주 분리)·완료판정(체결기반)·NYSE 휴장 게이트를 검증한다.
"""
import unittest
from datetime import date

from common.market_holidays import is_market_holiday
from trading.strategy.weekly_marker import _iso_week, completed


class TestIsoWeek(unittest.TestCase):
    def test_weekdays_share_key(self):
        # 같은 주 월(6/29)~금(7/3) → 동일 주차 키(그 주 1회만 매매)
        self.assertEqual(_iso_week(date(2026, 6, 29)), _iso_week(date(2026, 7, 3)))

    def test_next_week_differs(self):
        # 다음 주 월(7/6) → 다른 주차 키(다시 매매 가능)
        self.assertNotEqual(_iso_week(date(2026, 6, 29)), _iso_week(date(2026, 7, 6)))


class TestCompleted(unittest.TestCase):
    def test_no_buys_is_complete(self):
        # 신규 편입 없음(이미 목표 보유) → 체결 없어도 완료
        self.assertTrue(completed({"targets": ["AAPL"], "buys": []}, []))

    def test_filled_is_complete(self):
        self.assertTrue(completed({"targets": ["AAPL"], "buys": ["AAPL"]},
                                  [{"symbol": "AAPL", "filled": True}]))

    def test_no_fill_not_complete(self):
        # 매수할 게 있는데 체결 0건(휴장·일시 실패) → 미완료 → 다음 평일 재시도
        self.assertFalse(completed({"targets": ["AAPL"], "buys": ["AAPL"]},
                                   [{"symbol": "AAPL", "filled": False}]))
        self.assertFalse(completed({"targets": ["AAPL"], "buys": ["AAPL"]}, []))

    def test_empty_targets_not_complete(self):
        # 랭킹 산출 실패(데이터 결손) → 미완료 → 그 주 마커 안 남기고 재시도
        self.assertFalse(completed({"targets": [], "buys": []}, []))


class TestMarketHoliday(unittest.TestCase):
    def test_us_holiday(self):
        # 2026-07-03 = Independence Day 관측(7/4 토 → 금 휴장)
        self.assertTrue(is_market_holiday("US", date(2026, 7, 3)))

    def test_us_trading_day(self):
        self.assertFalse(is_market_holiday("US", date(2026, 7, 6)))

    def test_kr_unset_defaults_open(self):
        # KR 휴장 셋은 공표된 연도(2026)만 수록 — 미수록 연도(2028)는 체결기반 재시도에 위임(개장 취급)
        self.assertFalse(is_market_holiday("KR", date(2028, 1, 1)))


if __name__ == "__main__":
    unittest.main()
