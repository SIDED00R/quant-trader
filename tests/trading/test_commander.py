"""앙상블 commander 순수 함수 검증 (decide·combined_for_bar — DB/Kafka 불필요)."""
import unittest
from decimal import Decimal

from trading.strategy.runners.commander import combined_for_bar, decide

_EQ = Decimal("1000000")     # 총자산 100만(계산 편의)
_PX = Decimal("1000")        # 가격
_ROSTER = ["a", "b", "c"]
_EQUAL = {"a": 1.0, "b": 1.0, "c": 1.0}


class TestDecide(unittest.TestCase):
    def test_target_zero_sells_all(self):
        self.assertEqual(decide(Decimal("10"), _PX, Decimal("0"), _EQ, 0.0, 0.5), ("SELL", Decimal("10")))

    def test_target_zero_no_position_holds(self):
        self.assertIsNone(decide(Decimal("0"), _PX, _EQ, _EQ, 0.0, 0.5))

    def test_invalid_price_or_equity(self):
        self.assertIsNone(decide(Decimal("1"), Decimal("0"), _EQ, _EQ, 0.5, 0.5))
        self.assertIsNone(decide(Decimal("1"), _PX, _EQ, Decimal("0"), 0.5, 0.5))

    def test_enter_buys_toward_target(self):
        # 현금 100만·미보유, 목표 50% → 약 50만 매수(수수료 여유분 차감)
        order = decide(Decimal("0"), _PX, _EQ, _EQ, 0.5, 0.5)
        self.assertEqual(order[0], "BUY")
        self.assertTrue(Decimal("499") < order[1] < Decimal("500"))   # ~499.75

    def test_within_band_holds(self):
        # 보유=목표(50%) → 드리프트 0 < band → 유지
        self.assertIsNone(decide(Decimal("500"), _PX, Decimal("500000"), _EQ, 0.5, 0.5))

    def test_breach_band_reduces(self):
        # 보유 60% 인데 목표 10% → 드리프트 0.5 > 0.1*0.5 → 차액 매도
        order = decide(Decimal("600"), _PX, Decimal("400000"), _EQ, 0.1, 0.5)
        self.assertEqual(order[0], "SELL")
        self.assertEqual(order[1], Decimal("500"))   # (600k-100k)/1000

    def test_buy_capped_by_cash(self):
        # 목표 80%(=80만)인데 현금 30만뿐 → 매수는 현금 한도
        order = decide(Decimal("0"), _PX, Decimal("300000"), _EQ, 0.8, 0.5)
        self.assertEqual(order[0], "BUY")
        self.assertTrue(order[1] * _PX <= Decimal("300000"))   # 현금 초과 안 함

    def test_min_order_suppressed(self):
        # 목표가 현재보다 미세하게만 커서 차액<최소주문(5000) → 주문 없음
        self.assertIsNone(decide(Decimal("500"), _PX, _EQ, _EQ, 0.5005, 0.0))


class TestCombinedForBar(unittest.TestCase):
    def test_equal_weight_is_mean(self):
        # 동일가중 → 부하 목표의 평균(현 EnsembleStrategy 동작 보존)
        latest = {"a": ("d1", 1.0), "b": ("d1", 0.0), "c": ("d1", 0.5)}
        self.assertAlmostEqual(combined_for_bar(latest, _ROSTER, "d1", _EQUAL), 0.5)

    def test_incomplete_roster_returns_none(self):
        # 한 부하 미보고 → 합성 보류(조기 발주 방지)
        latest = {"a": ("d1", 1.0), "b": ("d1", 1.0)}
        self.assertIsNone(combined_for_bar(latest, _ROSTER, "d1", _EQUAL))

    def test_stale_bar_returns_none(self):
        # 한 부하가 직전 봉 신호만 가짐 → 같은 봉 아님 → 보류
        latest = {"a": ("d1", 1.0), "b": ("d1", 1.0), "c": ("d0", 1.0)}
        self.assertIsNone(combined_for_bar(latest, _ROSTER, "d1", _EQUAL))

    def test_weighted_combination(self):
        # 가중치 2:1:1, 목표 1/0/0 → (2*1)/(4) = 0.5
        latest = {"a": ("d1", 1.0), "b": ("d1", 0.0), "c": ("d1", 0.0)}
        self.assertAlmostEqual(
            combined_for_bar(latest, _ROSTER, "d1", {"a": 2.0, "b": 1.0, "c": 1.0}), 0.5)

    def test_zero_weight_sum_returns_none(self):
        # 순수 함수의 0 나눗셈 방어(런타임에선 load_weights가 합0이면 equal 반환해 도달 안 함)
        latest = {"a": ("d1", 1.0), "b": ("d1", 1.0), "c": ("d1", 1.0)}
        self.assertIsNone(combined_for_bar(latest, _ROSTER, "d1", {"a": 0.0, "b": 0.0, "c": 0.0}))


if __name__ == "__main__":
    unittest.main()
