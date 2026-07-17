"""페이퍼 장부 검증 (kr_fee 세금 포함·simulate_fill 위임 — DB 없이 mock)."""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading.portfolio import paper_ledger


class TestKrFee(unittest.TestCase):
    def test_buy_is_commission_only(self):
        self.assertEqual(paper_ledger.kr_fee("BUY", 1000, 10), Decimal("5.0000"))   # 10000×0.0005

    def test_sell_includes_tax(self):
        self.assertEqual(paper_ledger.kr_fee("SELL", 1000, 10), Decimal("25.0000"))  # 10000×(0.0005+0.0020)


class TestSimulateFill(unittest.TestCase):
    @patch("trading.portfolio.paper_ledger.apply_execution", return_value="applied")
    def test_inserts_order_and_passes_kr_fee(self, mock_apply):
        conn = MagicMock()
        res = paper_ledger.simulate_fill(conn, "kr_ichimoku", "005930", "SELL", 3, Decimal("70000"))
        self.assertEqual(res, "applied")
        self.assertTrue(conn.execute.called)                 # orders(PENDING) INSERT
        ex = mock_apply.call_args.args[1]
        self.assertEqual(ex["account_id"], "kr_ichimoku")
        self.assertEqual((ex["symbol"], ex["side"], ex["quantity"]), ("005930", "SELL", 3))
        self.assertEqual(ex["fee"], paper_ledger.kr_fee("SELL", Decimal("70000"), 3))  # 매도세 포함


if __name__ == "__main__":
    unittest.main()
