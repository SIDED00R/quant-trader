"""US 주간 모의 리밸런싱 execute() 검증 (trading/strategy/runners/us_trade_once.py, DB/네트워크 무접촉).

build_plan(=plan())·시세·브로커·마커는 전부 uto 네임스페이스 attr 패치로 대역한다.
"""
import unittest
from datetime import date
from unittest.mock import patch

from trading.strategy.runners import us_trade_once as uto

_FAKE_PLAN = {
    "bar": "2026-07-20", "cash": 0.0, "n_held": 1,
    "bal": {"cash": 10000.0, "positions": [{"symbol": "MSFT", "eval": 5000.0, "qty": 10}]},
    "targets": ["AAPL", "MSFT"],
    "buys": ["AAPL"],
    "sells": [],
    "ranked": None,
}


class TestDryRun(unittest.TestCase):
    def test_dry_run_skips_guards_and_orders(self):
        with patch.object(uto, "plan", return_value=_FAKE_PLAN) as mock_plan, \
             patch.object(uto, "weekly_guard") as mock_guard, \
             patch.object(uto, "market_open") as mock_open, \
             patch.object(uto, "refresh") as mock_refresh, \
             patch.object(uto, "place_and_chase") as mock_paq:
            r = uto.execute(top_n=2, live=False)

        mock_plan.assert_called_once_with(top_n=2, macro=False)
        mock_guard.assert_not_called()
        mock_open.assert_not_called()
        mock_refresh.assert_not_called()
        mock_paq.assert_not_called()
        self.assertEqual(r["placed"], [])
        self.assertIsNone(r["skipped"])


class TestWeeklyGuardSkip(unittest.TestCase):
    def test_guard_reason_short_circuits(self):
        with patch.object(uto, "weekly_guard", return_value="이미 이번주 리밸런싱 완료(2026-07-20)") as mock_guard, \
             patch.object(uto, "market_open") as mock_open, \
             patch.object(uto, "refresh") as mock_refresh, \
             patch.object(uto, "plan") as mock_plan:
            r = uto.execute(top_n=2, live=True)

        mock_guard.assert_called_once_with("US")
        mock_open.assert_not_called()
        mock_refresh.assert_not_called()
        mock_plan.assert_not_called()
        self.assertEqual(r["skipped"], "이미 이번주 리밸런싱 완료(2026-07-20)")


class TestMarketCloseSoonSkip(unittest.TestCase):
    def test_ttc_below_threshold_skips(self):
        with patch.object(uto, "weekly_guard", return_value=None), \
             patch.object(uto, "market_open", return_value=True), \
             patch.object(uto, "market_seconds_to_close", return_value=100), \
             patch.object(uto, "refresh") as mock_refresh, \
             patch.object(uto, "plan") as mock_plan:
            r = uto.execute(top_n=2, live=True)

        mock_refresh.assert_not_called()
        mock_plan.assert_not_called()
        self.assertEqual(r["skipped"], "US 마감 임박(100s) — 다음 평일 재시도")


class TestLiveSizingAndOrders(unittest.TestCase):
    def test_full_flow_sizes_and_places_orders(self):
        prices = {"AAPL": (150.0, "NASD")}
        today = date(2026, 7, 20)

        with patch.object(uto, "weekly_guard", return_value=None), \
             patch.object(uto, "market_open", return_value=True), \
             patch.object(uto, "market_seconds_to_close", return_value=400), \
             patch.object(uto, "refresh") as mock_refresh, \
             patch.object(uto, "plan", return_value=_FAKE_PLAN) as mock_plan, \
             patch.object(uto, "market_today", return_value=today), \
             patch.object(uto, "latest_closes", return_value={"AAPL": 149.0}), \
             patch.object(uto, "price_and_exchange", side_effect=lambda s: prices[s]), \
             patch.object(uto, "place_and_chase",
                          return_value={"status": "FILLED", "filled_qty": 49, "attempts": []}) as mock_paq, \
             patch.object(uto, "completed", return_value=True) as mock_completed, \
             patch.object(uto, "mark_week_done") as mock_mark:
            r = uto.execute(top_n=2, live=True, max_orders=5)

        mock_refresh.assert_called_once_with(["US"], log=print)
        mock_plan.assert_called_once_with(top_n=2, macro=False)
        # equity = cash(10000) + eval(5000) = 15000, per = 15000/2 = 7500
        # limit = round(150.0*1.02, 2) = 153.0, qty = int(7500 // 153.0) = 49
        mock_paq.assert_called_once_with("US", "AAPL", "BUY", 49, ref_price=150.0, exchange="NASD")
        self.assertEqual(r["placed"], [{
            "symbol": "AAPL", "qty": 49, "status": "FILLED", "attempts": [],
            "accepted": True, "msg": None, "filled_qty": 49, "filled": True,
        }])
        mock_completed.assert_called_once_with(_FAKE_PLAN, r["placed"])
        mock_mark.assert_called_once_with("US", today)
        self.assertIsNone(r["skipped"])


class TestUnresolvedSymbolContinues(unittest.TestCase):
    def test_missing_price_marks_not_accepted_and_continues(self):
        plan_two_buys = {**_FAKE_PLAN, "buys": ["AAPL", "BADSYM"]}

        def _px(sym):
            return (150.0, "NASD") if sym == "AAPL" else (None, None)

        with patch.object(uto, "weekly_guard", return_value=None), \
             patch.object(uto, "market_open", return_value=True), \
             patch.object(uto, "market_seconds_to_close", return_value=400), \
             patch.object(uto, "refresh"), \
             patch.object(uto, "plan", return_value=plan_two_buys), \
             patch.object(uto, "market_today", return_value=date(2026, 7, 20)), \
             patch.object(uto, "latest_closes", return_value={}), \
             patch.object(uto, "price_and_exchange", side_effect=_px), \
             patch.object(uto, "place_and_chase",
                          return_value={"status": "FILLED", "filled_qty": 49, "attempts": []}) as mock_paq, \
             patch.object(uto, "completed", return_value=True), \
             patch.object(uto, "mark_week_done"):
            r = uto.execute(top_n=2, live=True, max_orders=5)

        mock_paq.assert_called_once()   # BADSYM은 주문 미발행
        bad = next(o for o in r["placed"] if o["symbol"] == "BADSYM")
        self.assertFalse(bad["accepted"])
        self.assertEqual(bad["msg"], "시세/거래소 미확인")
        self.assertEqual(bad["filled_qty"], 0)


if __name__ == "__main__":
    unittest.main()
