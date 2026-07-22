"""체결 엔진 순수부 검증 (limit_fill_price/execute/load_pending_orders — run 루프·실 Kafka 제외).

핵심 계약: ① 지정가 체결 경계(BUY는 시장가<=지정가, SELL은 시장가>=지정가에서만 체결, 체결가는
항상 시장가) ② execution_id는 order_id 기반 uuid5 결정적 값(재소비 멱등의 근거)·fee 양자화·
발행 토픽/키 ③ 기동 재적재 시 MARKET/LIMIT 분류.
"""
import json
import unittest
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from common.config import FEE_RATE, TOPIC_EXECUTIONS
from tests.helpers import fake_pool
from trading.engine import matching


class TestLimitFillPrice(unittest.TestCase):
    def test_buy_fills_at_or_below_limit(self):
        order = {"side": "BUY", "price": "100"}
        self.assertEqual(matching.limit_fill_price(order, Decimal("100")), Decimal("100"))
        self.assertEqual(matching.limit_fill_price(order, Decimal("99")), Decimal("99"))

    def test_buy_not_filled_above_limit(self):
        order = {"side": "BUY", "price": "100"}
        self.assertIsNone(matching.limit_fill_price(order, Decimal("100.01")))

    def test_sell_fills_at_or_above_limit(self):
        order = {"side": "SELL", "price": "100"}
        self.assertEqual(matching.limit_fill_price(order, Decimal("100")), Decimal("100"))
        self.assertEqual(matching.limit_fill_price(order, Decimal("101")), Decimal("101"))

    def test_sell_not_filled_below_limit(self):
        order = {"side": "SELL", "price": "100"}
        self.assertIsNone(matching.limit_fill_price(order, Decimal("99.99")))


class TestExecuteDeterministicId(unittest.TestCase):
    def test_execution_id_deterministic_for_same_order(self):
        order = {"order_id": "ord-1", "account_id": "acc1", "symbol": "BTC",
                 "side": "BUY", "type": "MARKET", "quantity": "2"}
        producer1, producer2 = MagicMock(), MagicMock()
        matching.execute(order, Decimal("100"), producer1)
        matching.execute(order, Decimal("100"), producer2)

        payload1 = json.loads(producer1.produce.call_args.kwargs["value"])
        payload2 = json.loads(producer2.produce.call_args.kwargs["value"])
        expected_id = str(uuid.uuid5(matching.EXEC_NAMESPACE, "ord-1"))
        self.assertEqual(payload1["execution_id"], expected_id)
        self.assertEqual(payload1["execution_id"], payload2["execution_id"])


class TestExecuteFeeAndTopic(unittest.TestCase):
    def test_fee_quantized_and_topic_key_encoding(self):
        order = {"order_id": "ord-2", "account_id": "acc1", "symbol": "ETH",
                 "side": "SELL", "type": "LIMIT", "quantity": "3"}
        producer = MagicMock()
        price = Decimal("200")
        matching.execute(order, price, producer)

        args, kwargs = producer.produce.call_args
        self.assertEqual(args[0], TOPIC_EXECUTIONS)
        self.assertEqual(kwargs["key"], b"ETH")

        payload = json.loads(kwargs["value"])
        expected_fee = (price * Decimal("3") * FEE_RATE).quantize(Decimal("0.0001"))
        self.assertEqual(Decimal(str(payload["fee"])), expected_fee)


class TestLoadPendingOrders(unittest.TestCase):
    def test_classifies_market_and_limit_by_symbol(self):
        pool, conn = fake_pool()
        rows = [
            ("o1", "acc1", "BTC", "BUY", "MARKET", None, Decimal("1")),
            ("o2", "acc1", "ETH", "SELL", "LIMIT", Decimal("200"), Decimal("2")),
        ]
        conn.execute.return_value.fetchall.return_value = rows

        with patch.object(matching, "open_pool"), patch.object(matching, "close_pool"), \
             patch.object(matching, "pool", pool):
            pending, limit_orders = matching.load_pending_orders()

        self.assertIn("BTC", pending)
        self.assertEqual(pending["BTC"][0]["order_id"], "o1")
        self.assertNotIn("BTC", limit_orders)

        self.assertIn("ETH", limit_orders)
        self.assertEqual(limit_orders["ETH"][0]["side"], "SELL")
        self.assertNotIn("ETH", pending)


if __name__ == "__main__":
    unittest.main()
