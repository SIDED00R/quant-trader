"""order_writer 단위 테스트 — orders+order_outbox 원자적 INSERT (가짜 pool, DB/네트워크 없음).

핵심 계약: ① orders/outbox INSERT가 같은 conn(단일 트랜잭션, pool.connection() 1회)에서
실행돼 DB-Kafka dual-write 갭이 없음 ② 반환 order_id가 orders 파라미터·outbox payload(JSON)의
order_id와 정합 ③ payload에 symbol/side/quantity 직렬화 ④ price=None(MARKET) 처리.
"""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from common import order_writer
from tests.helpers import fake_pool


class TestPlaceOrder(unittest.TestCase):
    def test_dual_write_single_transaction(self):
        """orders/outbox INSERT가 같은 conn(단일 pool.connection())에서 실행된다."""
        pool, conn = fake_pool()
        with patch.object(order_writer, "pool", pool):
            order_writer.place_order("acc1", "005930", "BUY", "LIMIT", Decimal("10"), price=Decimal("70000"))
        self.assertEqual(pool.connection.call_count, 1)
        self.assertEqual(conn.execute.call_count, 2)

    def test_order_id_consistent_with_outbox_payload(self):
        """반환 order_id가 orders INSERT 파라미터·outbox payload(JSON)의 order_id와 일치한다."""
        pool, conn = fake_pool()
        with patch.object(order_writer, "pool", pool):
            order_id = order_writer.place_order(
                "acc1", "005930", "BUY", "LIMIT", Decimal("10"), price=Decimal("70000"))

        orders_sql, orders_params = conn.execute.call_args_list[0].args
        outbox_sql, outbox_params = conn.execute.call_args_list[1].args
        self.assertIn("INSERT INTO orders", orders_sql)
        self.assertIn("INSERT INTO order_outbox", outbox_sql)
        self.assertEqual(str(orders_params[0]), order_id)
        self.assertEqual(outbox_params[0], orders_params[0])   # 같은 order_id로 outbox 기록

        payload = json.loads(outbox_params[2])
        self.assertEqual(payload["order_id"], order_id)

    def test_payload_serializes_symbol_side_quantity(self):
        """outbox payload에 symbol/side/quantity가 직렬화된다."""
        pool, conn = fake_pool()
        with patch.object(order_writer, "pool", pool):
            order_writer.place_order("acc1", "AAPL", "SELL", "MARKET", Decimal("3"))

        _, outbox_params = conn.execute.call_args_list[1].args
        payload = json.loads(outbox_params[2])
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertEqual(payload["side"], "SELL")
        self.assertEqual(payload["quantity"], "3")

    def test_market_order_price_none(self):
        """price 생략(MARKET) — orders 파라미터·outbox payload 모두 price=None."""
        pool, conn = fake_pool()
        with patch.object(order_writer, "pool", pool):
            order_writer.place_order("acc1", "AAPL", "BUY", "MARKET", Decimal("1"))

        _, orders_params = conn.execute.call_args_list[0].args
        self.assertIsNone(orders_params[5])   # price 컬럼

        _, outbox_params = conn.execute.call_args_list[1].args
        payload = json.loads(outbox_params[2])
        self.assertIsNone(payload["price"])


if __name__ == "__main__":
    unittest.main()
