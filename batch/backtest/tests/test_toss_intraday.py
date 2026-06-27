"""토스 분봉 수집기 행 변환 검증 (네트워크 불필요 — 순수 함수)."""
import unittest
from datetime import datetime, timezone

from batch.backtest.toss_intraday import _row, _ts_utc


class TestTossIntradayRow(unittest.TestCase):
    def test_kst_timestamp_to_utc(self):
        c = {"timestamp": "2026-06-26T20:00:00.000+09:00", "openPrice": "1",
             "highPrice": "1", "lowPrice": "1", "closePrice": "1", "volume": "0", "currency": "KRW"}
        self.assertEqual(_ts_utc(c), datetime(2026, 6, 26, 11, 0, tzinfo=timezone.utc))  # 20:00 KST → 11:00 UTC

    def test_kr_row_fields_and_floats(self):
        c = {"timestamp": "2026-06-26T09:30:00.000+09:00", "openPrice": "339500",
             "highPrice": "340000", "lowPrice": "339000", "closePrice": "339800",
             "volume": "83769", "currency": "KRW"}
        row = _row("005930", c)
        self.assertEqual(row[0], "005930")
        self.assertEqual(row[1], datetime(2026, 6, 26, 0, 30, tzinfo=timezone.utc))  # 09:30 KST → 00:30 UTC
        self.assertEqual(row[2:7], [339500.0, 340000.0, 339000.0, 339800.0, 83769.0])
        self.assertEqual((row[7], row[8]), ("KRW", "KR"))

    def test_us_market_mapping(self):
        c = {"timestamp": "2026-06-27T23:30:00.000+09:00", "openPrice": "200",
             "highPrice": "201", "lowPrice": "199", "closePrice": "200.5", "volume": "1000", "currency": "USD"}
        row = _row("AAPL", c)
        self.assertEqual((row[7], row[8]), ("USD", "US"))
        self.assertEqual(row[1], datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc))  # 23:30 KST → 14:30 UTC


if __name__ == "__main__":
    unittest.main()
