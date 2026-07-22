"""수동 주식 주문 실행기 동기 유닛 검증 (api/stock_order_executor.py — _execute/_resolve/_recover_orphans).

asyncio 루프(run())는 제외 — 동기 헬퍼만 DB/네트워크 무접촉으로 검증한다.
"""
import unittest
from unittest.mock import patch

from api import stock_order_executor as executor
from tests.helpers import fake_pool


class TestExecuteAmountConversion(unittest.TestCase):
    """amount→qty 환산(US) → place_and_chase 인자 + FILLED 시 UPDATE."""

    def setUp(self):
        self.pool, self.conn = fake_pool()
        self.patcher_pool = patch.object(executor, "pool", self.pool)
        self.patcher_pool.start()
        self.addCleanup(self.patcher_pool.stop)

    def test_amount_converted_and_filled(self):
        row = (1, "US", "AAPL", "BUY", None, 1000.0)
        with patch.object(executor, "price_and_exchange", return_value=(100.0, "NASD")) as mock_px, \
             patch.object(executor, "place_and_chase",
                          return_value={"status": "FILLED", "filled_qty": 10, "attempts": [{"qty": 10}]}) as mock_paq, \
             patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._execute(row)

        mock_px.assert_called_once_with("AAPL")
        mock_paq.assert_called_once_with("US", "AAPL", "BUY", 10, ref_price=100.0, exchange="NASD")
        mock_notify.assert_not_called()

        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[0], "FILLED")
        self.assertEqual(params[2], 1)
        detail = params[1].obj
        self.assertEqual(detail["resolved_qty"], 10)
        self.assertEqual(detail["ref_price"], 100.0)
        self.assertEqual(detail["filled_qty"], 10)


class TestExecutePriceUnresolved(unittest.TestCase):
    """시세 미확인 → FAILED + 텔레그램 통보 + 주문 미발행(예약주문 무음유실 방지)."""

    def setUp(self):
        self.pool, self.conn = fake_pool()
        self.patcher_pool = patch.object(executor, "pool", self.pool)
        self.patcher_pool.start()
        self.addCleanup(self.patcher_pool.stop)

    def test_no_price_fails_without_placing_order(self):
        row = (2, "KR", "005930", "BUY", None, 500000.0)
        with patch.object(executor, "current_price", return_value=None), \
             patch.object(executor, "latest_closes", return_value={}), \
             patch.object(executor, "place_and_chase") as mock_paq, \
             patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._execute(row)

        mock_paq.assert_not_called()
        mock_notify.assert_called_once()
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[0], "FAILED")
        self.assertIn("ValueError", params[1].obj["error"])


class TestExecutePlaceAndChaseRaises(unittest.TestCase):
    """place_and_chase 예외 → FAILED 기록 + 통보(PLACED 고착 방지)."""

    def setUp(self):
        self.pool, self.conn = fake_pool()
        self.patcher_pool = patch.object(executor, "pool", self.pool)
        self.patcher_pool.start()
        self.addCleanup(self.patcher_pool.stop)

    def test_broker_exception_fails_order(self):
        row = (3, "US", "TSLA", "BUY", 5, None)
        with patch.object(executor, "price_and_exchange", return_value=(200.0, "NASD")), \
             patch.object(executor, "place_and_chase", side_effect=RuntimeError("boom")), \
             patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._execute(row)

        mock_notify.assert_called_once()
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[0], "FAILED")
        self.assertEqual(params[1].obj["error"], "RuntimeError: boom")


class TestExecuteFillOutcomes(unittest.TestCase):
    """부분체결 → FILLED+detail.partial / 전량 미체결 → FAILED."""

    def setUp(self):
        self.pool, self.conn = fake_pool()
        self.patcher_pool = patch.object(executor, "pool", self.pool)
        self.patcher_pool.start()
        self.addCleanup(self.patcher_pool.stop)

    def test_partial_fill_marked_filled_with_flag(self):
        row = (4, "US", "AAPL", "BUY", 10, None)
        r = {"status": "PARTIAL", "filled_qty": 4, "requested_qty": 10, "attempts": [{}]}
        with patch.object(executor, "price_and_exchange", return_value=(100.0, "NASD")), \
             patch.object(executor, "place_and_chase", return_value=r), \
             patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._execute(row)

        mock_notify.assert_not_called()
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[0], "FILLED")
        self.assertTrue(params[1].obj["partial"])

    def test_zero_fill_marked_failed_and_notified(self):
        row = (5, "US", "AAPL", "BUY", 10, None)
        r = {"status": "REJECTED", "filled_qty": 0, "requested_qty": 10,
             "attempts": [{"error": "장외 거부"}]}
        with patch.object(executor, "price_and_exchange", return_value=(100.0, "NASD")), \
             patch.object(executor, "place_and_chase", return_value=r), \
             patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._execute(row)

        mock_notify.assert_called_once()
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[0], "FAILED")


class TestRecoverOrphans(unittest.TestCase):
    """행 반환 시 경고 통보, SQL에 status='PLACED'·30분 포함."""

    def setUp(self):
        self.pool, self.conn = fake_pool()
        self.patcher_pool = patch.object(executor, "pool", self.pool)
        self.patcher_pool.start()
        self.addCleanup(self.patcher_pool.stop)

    def test_orphans_found_notifies(self):
        self.conn.execute.return_value.fetchall.return_value = [(11,), (12,)]
        with patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._recover_orphans()

        mock_notify.assert_called_once()
        sql = self.conn.execute.call_args[0][0]
        self.assertIn("status='PLACED'", sql)
        self.assertIn("30 minutes", sql)

    def test_no_orphans_no_notify(self):
        self.conn.execute.return_value.fetchall.return_value = []
        with patch.object(executor.notify_telegram, "send") as mock_notify:
            executor._recover_orphans()

        mock_notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
