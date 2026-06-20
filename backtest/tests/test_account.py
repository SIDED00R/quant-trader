"""BacktestAccount 체결 수학 검증 (portfolio.updater와 동일 가정)."""
import unittest
from decimal import Decimal

from backtest.account import BacktestAccount


class TestAccount(unittest.TestCase):
    def test_buy_then_sell_roundtrip(self):
        acct = BacktestAccount(Decimal("1000000"))

        ok = acct.apply_buy("X", Decimal("100"), Decimal("10"), Decimal("5"), ts=0.0)
        self.assertTrue(ok)
        self.assertEqual(acct.cash, Decimal("998995"))          # 1,000,000 - (100*10 + 5)
        self.assertEqual(acct.qty("X"), Decimal("10"))
        self.assertEqual(acct.avg("X"), Decimal("100.5"))        # 수수료 포함 취득단가 1005/10
        # 평가자산 = 현금 + 보유*현재가 (= 초기 - 수수료)
        self.assertEqual(acct.equity({"X": Decimal("100")}), Decimal("999995"))

        trade = acct.apply_sell("X", Decimal("110"), Decimal("10"), Decimal("5.5"), ts=10.0)
        self.assertIsNotNone(trade)
        self.assertEqual(acct.cash, Decimal("1000089.5"))        # +110*10 - 5.5
        self.assertEqual(acct.qty("X"), Decimal("0"))
        self.assertEqual(trade.pnl, Decimal("89.5"))             # 1094.5 - 1005
        self.assertEqual(trade.entry_price, Decimal("100"))
        self.assertEqual(trade.exit_price, Decimal("110"))
        self.assertEqual(trade.buy_fee, Decimal("5"))
        self.assertEqual(trade.sell_fee, Decimal("5.5"))

    def test_buy_rejected_when_insufficient_cash(self):
        acct = BacktestAccount(Decimal("100"))
        ok = acct.apply_buy("Y", Decimal("100"), Decimal("10"), Decimal("5"), ts=0.0)
        self.assertFalse(ok)
        self.assertEqual(acct.cash, Decimal("100"))
        self.assertEqual(acct.qty("Y"), Decimal("0"))

    def test_sell_rejected_when_no_holdings(self):
        acct = BacktestAccount(Decimal("1000"))
        res = acct.apply_sell("Z", Decimal("100"), Decimal("1"), Decimal("0"), ts=0.0)
        self.assertIsNone(res)
        self.assertEqual(acct.cash, Decimal("1000"))

    def test_partial_sells_prorate_entry_fee(self):
        # 부분매도 시 진입수수료가 수량 비례로 배분 → Σbuy_fee == 실제 지불 진입수수료(중복계상 방지)
        acct = BacktestAccount(Decimal("100000"))
        acct.apply_buy("X", Decimal("1000"), Decimal("10"), Decimal("5"), ts=0.0)  # entry_fee=5, qty=10
        t1 = acct.apply_sell("X", Decimal("1000"), Decimal("3"), Decimal("1.5"), ts=1.0)
        t2 = acct.apply_sell("X", Decimal("1000"), Decimal("3"), Decimal("1.5"), ts=2.0)
        t3 = acct.apply_sell("X", Decimal("1000"), Decimal("4"), Decimal("2.0"), ts=3.0)  # 전량 청산
        self.assertEqual(t1.buy_fee + t2.buy_fee + t3.buy_fee, Decimal("5"))  # 진입수수료 합 == 5(중복 없음)
        self.assertEqual(t3.buy_fee, Decimal("2.0000"))  # 마지막 매도는 잔여 entry_fee 전액

    def test_full_sell_keeps_full_entry_fee(self):
        acct = BacktestAccount(Decimal("100000"))
        acct.apply_buy("X", Decimal("1000"), Decimal("10"), Decimal("5"), ts=0.0)
        t = acct.apply_sell("X", Decimal("1000"), Decimal("10"), Decimal("5"), ts=1.0)
        self.assertEqual(t.buy_fee, Decimal("5"))  # 전량 1회 청산 → 진입수수료 전액(회귀 보존)


if __name__ == "__main__":
    unittest.main()
