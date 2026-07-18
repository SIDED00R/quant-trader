"""관심종목 데일리 푸시 선택 로직 검증 (select_symbols — KR 우선·added_at·상한, 순수 함수)."""
import unittest
from datetime import datetime

from common.chart.watchlist_chart_telegram import MAX_SYMBOLS, select_symbols

D = datetime


class TestSelectSymbols(unittest.TestCase):
    def test_kr_first_then_added_order(self):
        rows = [("US", "AAPL", D(2026, 1, 3)), ("KR", "005930", D(2026, 1, 1)), ("KR", "000660", D(2026, 1, 2))]
        picks = [(m, s) for m, s, _ in select_symbols(rows)]
        self.assertEqual(picks, [("KR", "005930"), ("KR", "000660"), ("US", "AAPL")])

    def test_cap(self):
        rows = [("KR", f"{i:06d}", D(2026, 1, 1)) for i in range(MAX_SYMBOLS + 5)]
        self.assertEqual(len(select_symbols(rows)), MAX_SYMBOLS)

    def test_empty(self):
        self.assertEqual(select_symbols([]), [])


if __name__ == "__main__":
    unittest.main()
