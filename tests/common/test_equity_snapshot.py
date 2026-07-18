"""평가자산 스냅샷 훅 검증 (record_snapshot/record_stock_snapshot — 가짜 pool 주입, DB/네트워크 없음).

핵심 계약: ① upsert SQL(같은 날 재실행=마지막 승리) ② 어떤 예외도 밖으로 새지 않음(비치명 격리 —
매매 잡의 종료코드·텔레그램 통보를 절대 깨뜨리지 않는다) ③ 주식 잔고 합산(cash+Σeval)·통화 전달.
"""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from common.equity import equity_snapshot
from common.equity.equity_snapshot import KIS_ACCOUNT, record_snapshot, record_stock_snapshot


def _fake_pool():
    """pool.connection() 컨텍스트가 돌려줄 가짜 conn과 pool."""
    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    pool.connection.return_value.__exit__.return_value = False
    return pool, conn


class TestRecordSnapshot(unittest.TestCase):
    def test_upsert_sql_and_params(self):
        pool, conn = _fake_pool()
        with patch.object(equity_snapshot, "pool", pool), patch.object(equity_snapshot, "open_pool"):
            ok = record_snapshot("COIN", "demo", "KRW", Decimal("123.45"),
                                 cash=Decimal("23.45"), positions_value=Decimal("100"))
        self.assertTrue(ok)
        sql, params = conn.execute.call_args.args
        self.assertIn("ON CONFLICT (market, account_id, snap_date) DO UPDATE", sql)
        self.assertEqual(params[1:], ("COIN", "demo", "KRW",
                                      Decimal("23.45"), Decimal("100"), Decimal("123.45")))

    def test_db_error_swallowed(self):
        # 격리 계약의 핵심 — pool이 죽어도 예외가 새지 않고 False
        pool = MagicMock()
        pool.connection.side_effect = RuntimeError("pg down")
        with patch.object(equity_snapshot, "pool", pool), patch.object(equity_snapshot, "open_pool"):
            self.assertFalse(record_snapshot("KR", KIS_ACCOUNT, "KRW", 1.0))

    def test_open_pool_error_swallowed(self):
        with patch.object(equity_snapshot, "open_pool", side_effect=RuntimeError("dsn")):
            self.assertFalse(record_snapshot("US", KIS_ACCOUNT, "USD", 1.0))


class TestRecordStockSnapshot(unittest.TestCase):
    def test_sums_balance_and_delegates(self):
        pool, conn = _fake_pool()
        bal = {"market": "KR", "currency": "KRW", "cash": 1000.0,
               "positions": [{"eval": 500.0}, {"eval": 250.0}]}
        with patch.object(equity_snapshot, "pool", pool), patch.object(equity_snapshot, "open_pool"):
            ok = record_stock_snapshot("KR", lambda: bal)
        self.assertTrue(ok)
        _, params = conn.execute.call_args.args
        self.assertEqual(params[1:], ("KR", KIS_ACCOUNT, "KRW", 1000.0, 750.0, 1750.0))

    def test_balance_error_swallowed(self):
        def boom():
            raise RuntimeError("KIS 5xx")
        self.assertFalse(record_stock_snapshot("US", boom))


if __name__ == "__main__":
    unittest.main()
