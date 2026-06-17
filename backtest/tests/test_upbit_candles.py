"""업비트 분봉 캐시 로드/merge 검증 (합성 CSV, 네트워크 불필요)."""
import csv
import os
import tempfile
import unittest
from decimal import Decimal

from backtest.upbit_candles import _HEADER, cache_path, load


def _write(cache_dir, market, unit, rows):
    p = cache_path(cache_dir, market, unit)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for ts, close in rows:  # ts_ms(=window_start ms), close (나머지 OHLCV는 테스트에 불필요)
            w.writerow([ts, close, close, close, close, 0, "x"])


class TestUpbitCache(unittest.TestCase):
    def test_merge_global_time_order_and_tiebreak(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101"), (180000, "102")])
            _write(d, "KRW-ETH", 1, [(60000, "10"), (90000, "11")])
            out = list(load(["KRW-ETH", "KRW-BTC"], 1, d))  # 입력순을 일부러 ETH 먼저
            self.assertEqual(len(out), 5)
            ts = [t.ts for t in out]
            self.assertEqual(ts, sorted(ts), "전역 시간순이어야 함")
            # 동일 ts(60.0) tie-break는 입력순이 아니라 symbol 사전순(ts, symbol) → BTC 먼저
            self.assertEqual([(out[0].symbol, out[0].ts), (out[1].symbol, out[1].ts)],
                             [("KRW-BTC", 60.0), ("KRW-ETH", 60.0)])
            self.assertEqual([t for t in out if t.ts == 90.0][0].price, Decimal("11"))

    def test_range_filter_start_inclusive_end_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101"), (180000, "102")])
            out = list(load(["KRW-BTC"], 1, d, start_ms=120000, end_ms=180000))
            self.assertEqual([t.ts for t in out], [120.0])
            self.assertEqual(out[0].price, Decimal("101"))

    def test_missing_cache_yields_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list(load(["KRW-NOPE"], 1, d)), [])

    def test_unsorted_cache_raises(self):
        # finalize 안 된(시간 역순) 캐시는 조용히 잘못된 결과 대신 에러로 막는다
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(180000, "102"), (120000, "101"), (60000, "100")])
            with self.assertRaises(ValueError):
                list(load(["KRW-BTC"], 1, d))


if __name__ == "__main__":
    unittest.main()
