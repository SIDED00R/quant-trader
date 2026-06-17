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


if __name__ == "__main__":
    unittest.main()
