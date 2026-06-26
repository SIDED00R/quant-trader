"""성과지표 순수 함수 검증."""
import unittest
from decimal import Decimal

from batch.backtest.metrics import (
    annualized_sharpe,
    max_drawdown,
    total_return,
    trade_stats,
)
from batch.backtest.models import ClosedTrade


def _trade(pnl, entry_ts=0.0, exit_ts=5.0):
    p = Decimal(str(pnl))
    return ClosedTrade(
        symbol="X", qty=Decimal("1"), entry_price=Decimal("100"), exit_price=Decimal("110"),
        buy_fee=Decimal("0"), sell_fee=Decimal("0"), pnl=p, return_pct=Decimal("0"),
        reason="TAKE", entry_ts=entry_ts, exit_ts=exit_ts,
    )


class TestMetrics(unittest.TestCase):
    def test_total_return(self):
        self.assertEqual(total_return(Decimal("1000"), Decimal("1080")), Decimal("0.08"))
        self.assertEqual(total_return(Decimal("0"), Decimal("100")), Decimal("0"))

    def test_max_drawdown(self):
        mdd = max_drawdown([Decimal("1000"), Decimal("1100"), Decimal("900"), Decimal("1080")])
        self.assertAlmostEqual(float(mdd), 200 / 1100, places=9)
        self.assertEqual(max_drawdown([Decimal("100"), Decimal("110"), Decimal("120")]), Decimal("0"))
        self.assertEqual(max_drawdown([]), Decimal("0"))

    def test_trade_stats(self):
        s = trade_stats([_trade(10, 0, 5), _trade(-5, 0, 10)])
        self.assertEqual(s["num_trades"], 2)
        self.assertEqual(s["num_wins"], 1)
        self.assertEqual(s["num_losses"], 1)
        self.assertEqual(s["win_rate"], Decimal("0.5"))
        self.assertEqual(s["gross_profit"], Decimal("10"))
        self.assertEqual(s["gross_loss"], Decimal("5"))
        self.assertEqual(s["profit_factor"], Decimal("2"))
        self.assertEqual(s["avg_holding_sec"], 7.5)

    def test_trade_stats_empty(self):
        s = trade_stats([])
        self.assertEqual(s["num_trades"], 0)
        self.assertEqual(s["win_rate"], Decimal("0"))
        self.assertIsNone(s["profit_factor"])

    def test_sharpe_edge_cases(self):
        self.assertEqual(annualized_sharpe([100.0], 525600), 0.0)        # 표본 부족
        self.assertEqual(annualized_sharpe([100.0, 100.0, 100.0], 525600), 0.0)  # 변동 0
        self.assertGreater(annualized_sharpe([100.0, 101.0, 102.0, 103.0], 525600), 0.0)


if __name__ == "__main__":
    unittest.main()
