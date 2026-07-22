"""전략 부하 가중치 조회 검증 (load_weights — 가짜 pool 주입, DB 무접촉).

핵심 계약: ① ENSEMBLE_ADAPTIVE off면 동일가중을 즉시 반환하고 pool을 건드리지 않는다
② on이라도 일부 부하 미등록/가중치 합 0이면 안전하게 동일가중으로 폴백한다
③ on + 정상이면 테이블 값을 float로 변환해 반환한다.
"""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from common import strategy_weights
from common.strategy_weights import load_weights
from tests.helpers import fake_pool


class TestLoadWeightsAdaptiveOff(unittest.TestCase):
    def test_equal_weight_without_touching_pool(self):
        pool = MagicMock()
        with patch.object(strategy_weights, "ENSEMBLE_ADAPTIVE", False), \
             patch.object(strategy_weights, "pool", pool):
            result = load_weights(["a", "b"])
        self.assertEqual(result, {"a": 1.0, "b": 1.0})
        pool.connection.assert_not_called()


class TestLoadWeightsAdaptiveOnFallback(unittest.TestCase):
    def test_partial_registration_falls_back_to_equal(self):
        pool, conn = fake_pool()
        conn.execute.return_value.fetchall.return_value = [("a", Decimal("0.7"))]  # b 미등록
        with patch.object(strategy_weights, "ENSEMBLE_ADAPTIVE", True), \
             patch.object(strategy_weights, "pool", pool):
            result = load_weights(["a", "b"])
        self.assertEqual(result, {"a": 1.0, "b": 1.0})

    def test_zero_sum_falls_back_to_equal(self):
        pool, conn = fake_pool()
        conn.execute.return_value.fetchall.return_value = [("a", Decimal("0")), ("b", Decimal("0"))]
        with patch.object(strategy_weights, "ENSEMBLE_ADAPTIVE", True), \
             patch.object(strategy_weights, "pool", pool):
            result = load_weights(["a", "b"])
        self.assertEqual(result, {"a": 1.0, "b": 1.0})


class TestLoadWeightsAdaptiveOnNormal(unittest.TestCase):
    def test_normal_case_returns_table_values_as_float(self):
        pool, conn = fake_pool()
        conn.execute.return_value.fetchall.return_value = [("a", Decimal("0.3")), ("b", Decimal("0.7"))]
        with patch.object(strategy_weights, "ENSEMBLE_ADAPTIVE", True), \
             patch.object(strategy_weights, "pool", pool):
            result = load_weights(["a", "b"])
        self.assertEqual(result, {"a": 0.3, "b": 0.7})
        self.assertIsInstance(result["a"], float)
        sql, params = conn.execute.call_args.args
        self.assertIn("strategy_weights", sql)
        self.assertEqual(params, (["a", "b"],))


if __name__ == "__main__":
    unittest.main()
