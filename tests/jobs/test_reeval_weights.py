"""부하 재평가 잡 검증 (reeval/_save — score_loads/DB 무거운 의존을 목으로 대체).

핵심 계약: ① dry_run=True면 _save가 호출되지 않고, 반환 가중치는 compute_weights(기검증
순수함수)를 직접 호출한 산출과 일치한다 ② dry_run=False면 산출 가중치 그대로 _save가 호출된다
③ _save는 ON CONFLICT (strategy) DO UPDATE UPSERT + Decimal 변환 계약을 지킨다.
"""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from batch.jobs import reeval_weights
from common.config import (
    ENSEMBLE_DSR_GATE,
    ENSEMBLE_WEIGHT_CAP_MULT,
    ENSEMBLE_WEIGHT_EWMA,
    ENSEMBLE_WEIGHT_FLOOR_MULT,
)
from tests.helpers import fake_pool
from trading.strategy.core.weight_policy import compute_weights


def _silent(*args, **kwargs):
    pass


class TestReevalDryRun(unittest.TestCase):
    def test_dry_run_skips_save_and_matches_compute_weights(self):
        scored = {
            "trend-5-40": (0.8, 0.99),
            "trend-10-60": (0.3, 0.5),
            "trend-20-100": (-0.1, 0.99),
        }
        prev = {"trend-5-40": 0.4, "trend-10-60": 0.3, "trend-20-100": 0.3}

        with patch.object(reeval_weights, "score_loads", return_value=scored), \
             patch.object(reeval_weights, "_load_prev", return_value=prev), \
             patch.object(reeval_weights, "_save") as save_mock:
            weights = reeval_weights.reeval(
                [], [], Decimal("0"), None, 86400.0, 1.0, 1.0, 1.0,
                dry_run=True, log=_silent,
            )

        save_mock.assert_not_called()
        scores = {k: v[0] for k, v in scored.items()}
        gates = {k: v[1] for k, v in scored.items()}
        expected = compute_weights(
            scores, gates, prev,
            floor_mult=ENSEMBLE_WEIGHT_FLOOR_MULT, cap_mult=ENSEMBLE_WEIGHT_CAP_MULT,
            dsr_gate=ENSEMBLE_DSR_GATE, ewma_alpha=ENSEMBLE_WEIGHT_EWMA,
        )
        self.assertEqual(weights, expected)


class TestReevalPersists(unittest.TestCase):
    def test_non_dry_run_saves_computed_weights(self):
        scored = {"trend-5-40": (1.0, 0.99), "trend-10-60": (0.5, 0.99)}

        with patch.object(reeval_weights, "score_loads", return_value=scored), \
             patch.object(reeval_weights, "_load_prev", return_value={}), \
             patch.object(reeval_weights, "_save") as save_mock:
            weights = reeval_weights.reeval(
                [], [], Decimal("0"), None, 86400.0, 1.0, 1.0, 1.0,
                dry_run=False, log=_silent,
            )

        save_mock.assert_called_once_with(weights)


class TestSaveUpsertContract(unittest.TestCase):
    def test_upsert_sql_and_decimal_conversion(self):
        pool, conn = fake_pool()
        cur = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur

        with patch.object(reeval_weights, "pool", pool):
            reeval_weights._save({"trend-5-40": 0.123456789})

        sql, params = cur.executemany.call_args.args
        self.assertIn("ON CONFLICT (strategy) DO UPDATE", sql)
        self.assertEqual(params, [("trend-5-40", Decimal(str(round(0.123456789, 6))))])
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
