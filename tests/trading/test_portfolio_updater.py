"""포트폴리오 체결 반영 검증 (apply_execution — conn 직접 주입, DB/네트워크 무접촉).

핵심 계약: ① execution_id 중복은 멱등(상태 미변경) ② BUY 잔고부족/SELL 유령매도는 REJECTED
(executions 미기록) ③ BUY 정상은 cost=price*qty+fee 차감·평단=(기존+비용포함단가) 반영 후 FILLED
④ SELL 정상은 입금=price*qty-fee·수량 차감 후 FILLED.
"""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from trading.portfolio import updater


def _conn(fetchone_results):
    """conn.execute(...).fetchone() 호출 순서대로 결과를 반환하는 가짜 conn."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = fetchone_results
    return conn


def _ex(**overrides):
    base = {
        "execution_id": "e1", "order_id": "o1", "account_id": "a1", "symbol": "BTC",
        "side": "BUY", "price": "100", "quantity": "2", "fee": "1",
    }
    base.update(overrides)
    return base


class TestApplyExecutionDuplicate(unittest.TestCase):
    def test_duplicate_execution_id_is_idempotent(self):
        conn = _conn([(1,)])
        result = updater.apply_execution(conn, _ex())
        self.assertEqual(result, "duplicate")
        # 중복 확인 외 어떤 상태 변경 SQL도 실행되지 않아야 한다(멱등).
        self.assertEqual(conn.execute.call_count, 1)


class TestApplyExecutionBuyRejected(unittest.TestCase):
    def test_insufficient_balance_rejects_without_recording(self):
        conn = _conn([None, (Decimal("50"),)])  # 중복 아님, 잔고 50 < cost(201)
        result = updater.apply_execution(conn, _ex())
        self.assertEqual(result, "rejected")
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        self.assertFalse(any("INSERT INTO executions" in s for s in sqls))
        self.assertTrue(any("UPDATE orders SET status='REJECTED'" in s for s in sqls))


class TestApplyExecutionBuyApplied(unittest.TestCase):
    def test_success_deducts_balance_and_averages_position(self):
        conn = _conn([None, (Decimal("1000"),)])
        ex = _ex(price="100", quantity="2", fee="1")
        result = updater.apply_execution(conn, ex)
        self.assertEqual(result, "applied")

        cost = Decimal("100") * Decimal("2") + Decimal("1")  # 201
        deduct_call = next(
            c for c in conn.execute.call_args_list
            if "krw_balance = krw_balance - " in c.args[0]
        )
        self.assertEqual(deduct_call.args[1], (cost, "a1"))

        pos_call = next(
            c for c in conn.execute.call_args_list
            if c.args[0].strip().startswith("INSERT INTO positions")
        )
        self.assertEqual(pos_call.args[1], ("a1", "BTC", Decimal("2"), cost / Decimal("2")))

        self.assertTrue(any(
            "orders SET status='FILLED'" in c.args[0] for c in conn.execute.call_args_list
        ))


class TestApplyExecutionSellRejected(unittest.TestCase):
    def test_phantom_sell_rejects_without_recording(self):
        conn = _conn([None, (Decimal("0.5"),)])  # 보유 0.5 < 매도 수량 2
        ex = _ex(side="SELL")
        result = updater.apply_execution(conn, ex)
        self.assertEqual(result, "rejected")
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        self.assertFalse(any("INSERT INTO executions" in s for s in sqls))
        self.assertTrue(any("UPDATE orders SET status='REJECTED'" in s for s in sqls))


class TestApplyExecutionSellApplied(unittest.TestCase):
    def test_success_credits_balance_and_reduces_position(self):
        conn = _conn([None, (Decimal("5"),)])
        ex = _ex(side="SELL", price="100", quantity="2", fee="1")
        result = updater.apply_execution(conn, ex)
        self.assertEqual(result, "applied")

        credit_call = next(
            c for c in conn.execute.call_args_list
            if "krw_balance = krw_balance + " in c.args[0]
        )
        self.assertEqual(credit_call.args[1], (Decimal("199"), "a1"))  # 100*2-1

        dec_call = next(
            c for c in conn.execute.call_args_list
            if "quantity = quantity - " in c.args[0]
        )
        self.assertEqual(dec_call.args[1], (Decimal("2"), "a1", "BTC"))

        self.assertTrue(any(
            "orders SET status='FILLED'" in c.args[0] for c in conn.execute.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
