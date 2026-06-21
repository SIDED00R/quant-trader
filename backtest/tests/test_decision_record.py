"""매매 결정 분류 검증 (classify — decide 결과 → 행동·사유, 순수 함수 DB/Kafka 불필요)."""
import unittest
from decimal import Decimal

from strategy.decision_record import classify

D0 = Decimal("0")
D10 = Decimal("10")


class TestClassify(unittest.TestCase):
    def test_incomplete_signal_holds(self):
        # target_w=None(일부 부하 미보고) → 매매 안 함
        action, reason = classify(None, None, D0)
        self.assertEqual(action, "HOLD")
        self.assertIn("불완전", reason)

    def test_buy(self):
        action, reason = classify(("BUY", D10), 0.5, D0)
        self.assertEqual(action, "BUY")
        self.assertIn("매수", reason)

    def test_sell_reversal_full_exit(self):
        # 목표 0 + 보유분 매도 → 전량 청산
        action, reason = classify(("SELL", D10), 0.0, D10)
        self.assertEqual(action, "SELL")
        self.assertIn("청산", reason)

    def test_sell_trim(self):
        # 목표>0인데 비중 축소 매도 → 차액 매도(청산 아님)
        action, reason = classify(("SELL", D10), 0.3, D10)
        self.assertEqual(action, "SELL")
        self.assertIn("하향", reason)

    def test_hold_no_entry(self):
        # 목표<=0 & 주문 없음 → 무보유 현금 유지(보유 있었으면 decide가 매도)
        action, reason = classify(None, 0.0, D0)
        self.assertEqual(action, "HOLD")
        self.assertIn("추세 미진입", reason)

    def test_hold_within_band(self):
        # 목표>0 & 보유>0 & 주문 없음 → 밴드 내 유지
        action, reason = classify(None, 0.5, D10)
        self.assertEqual(action, "HOLD")
        self.assertIn("밴드", reason)

    def test_hold_below_min_order(self):
        # 목표>0 & 무보유 & 주문 없음 → 신규 진입액이 최소주문 미만
        action, reason = classify(None, 0.5, D0)
        self.assertEqual(action, "HOLD")
        self.assertIn("최소주문", reason)


if __name__ == "__main__":
    unittest.main()
