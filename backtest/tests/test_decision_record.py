"""매매결정 분류 검증 (classify — 매매 안 한 HOLD/SKIP 도 사유·수치 반환)."""
import unittest
from decimal import Decimal

from strategy.decision_record import classify

_EQ = Decimal("1000000")     # 총자산 100만
_PX = Decimal("1000")


class TestClassify(unittest.TestCase):
    def test_no_price_is_skip(self):
        d = classify(Decimal("1"), None, _EQ, _EQ, 0.5, 0.5)
        self.assertEqual(d.decision, "SKIP")
        self.assertIn("최신가", d.reason)
        self.assertEqual(d.quantity, Decimal(0))

    def test_invalid_price_is_skip(self):
        self.assertEqual(classify(Decimal("1"), Decimal("0"), _EQ, _EQ, 0.5, 0.5).decision, "SKIP")
        self.assertEqual(classify(Decimal("1"), _PX, _EQ, Decimal("0"), 0.5, 0.5).decision, "SKIP")

    def test_within_band_is_hold_with_numbers(self):
        # 보유=목표(50%) → 격차 0 < 밴드 → 유지, 수치 채워짐
        d = classify(Decimal("500"), _PX, Decimal("500000"), _EQ, 0.5, 0.5)
        self.assertEqual(d.decision, "HOLD")
        self.assertAlmostEqual(d.current_w, 0.5)
        self.assertAlmostEqual(d.target_w, 0.5)
        self.assertAlmostEqual(d.gap, 0.0)
        self.assertIn("유지", d.reason)

    def test_target_zero_no_position_is_hold(self):
        d = classify(Decimal("0"), _PX, _EQ, _EQ, 0.0, 0.5)
        self.assertEqual(d.decision, "HOLD")

    def test_target_zero_with_position_sells_all(self):
        d = classify(Decimal("10"), _PX, Decimal("0"), _EQ, 0.0, 0.5)
        self.assertEqual(d.decision, "SELL")
        self.assertEqual(d.quantity, Decimal("10"))

    def test_breach_band_buys(self):
        d = classify(Decimal("0"), _PX, _EQ, _EQ, 0.5, 0.5)
        self.assertEqual(d.decision, "BUY")
        self.assertGreater(d.quantity, 0)

    def test_breach_band_sells(self):
        d = classify(Decimal("600"), _PX, Decimal("400000"), _EQ, 0.1, 0.5)
        self.assertEqual(d.decision, "SELL")
        self.assertEqual(d.quantity, Decimal("500"))

    def test_min_order_suppressed_is_hold(self):
        # 차액 < 최소주문 → 매수 대신 유지(매매 안 함, 사유 기록)
        d = classify(Decimal("500"), _PX, _EQ, _EQ, 0.5005, 0.0)
        self.assertEqual(d.decision, "HOLD")
        self.assertIn("최소주문", d.reason)


if __name__ == "__main__":
    unittest.main()
