"""CSV→ClickHouse 적재 + datasource 테이블 화이트리스트 검증 (네트워크/Docker 불필요)."""
import tempfile
import unittest
from datetime import timezone

from backtest import csv_to_clickhouse as c2c
from backtest.datasource import load_clickhouse_candles
from backtest.tests.test_upbit_candles import _write


class _FakeCH:
    def __init__(self):
        self.inserts = []

    def insert(self, table, rows, column_names=None):
        self.inserts.append((table, list(rows)))


class TestCsvToClickhouse(unittest.TestCase):
    def test_rows_parse_ohlcv_and_utc_window(self):
        with tempfile.TemporaryDirectory() as d:
            # ts_ms=60000 → 1970-01-01T00:01:00 UTC, close=100
            from backtest.tests.test_upbit_candles import _write_rows
            _write_rows(d, "KRW-BTC", 1, [[60000, "99", "110", "98", "100", "5", "x"]])
            from backtest.upbit_candles import cache_path
            rows = list(c2c._rows(cache_path(d, "KRW-BTC", 1), "KRW-BTC"))
            self.assertEqual(len(rows), 1)
            sym, ws, o, h, lo, cl, v = rows[0]
            self.assertEqual((sym, o, h, lo, cl, v), ("KRW-BTC", 99.0, 110.0, 98.0, 100.0, 5.0))
            self.assertEqual(ws.tzinfo, timezone.utc)
            self.assertEqual(ws.minute, 1)

    def test_load_inserts_batches_and_counts(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101"), (180000, "102")])
            ch = _FakeCH()
            n = c2c.load_csv_to_clickhouse("KRW-BTC", 1, d, ch, log=lambda *a: None)
            self.assertEqual(n, 3)
            self.assertEqual(ch.inserts[0][0], "candles_1m")
            self.assertEqual(sum(len(r) for _, r in ch.inserts), 3)

    def test_missing_cache_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(c2c.load_csv_to_clickhouse("KRW-NOPE", 1, d, _FakeCH(), log=lambda *a: None), 0)


class TestDatasourceTableWhitelist(unittest.TestCase):
    def test_bad_table_rejected_before_connect(self):
        # 화이트리스트 밖 테이블명은 연결 시도 전에 ValueError (SQL 주입 차단)
        with self.assertRaises(ValueError):
            list(load_clickhouse_candles(table="candles_1m; DROP TABLE x"))


if __name__ == "__main__":
    unittest.main()
