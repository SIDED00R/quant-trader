"""종목 차트 조립 검증 (KR=주봉+일목 캡션 / US=일봉 / 봉 부족 예외)."""
import unittest
from datetime import date, timedelta

from common.chart.symbol_chart import chart_for_symbol


def _daily(n=160):
    out, p = [], 1000.0
    for i in range(n):
        d = date(2024, 1, 1) + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        p *= 1.01 if i % 6 < 4 else 0.985
        out.append((d, p, p * 1.02, p * 0.98, p * 1.003))
    return out


class TestChartForSymbol(unittest.TestCase):
    def test_kr_weekly_ichimoku_caption(self):
        png, cap = chart_for_symbol(_daily(), "KR", "005930", "삼성전자")
        self.assertEqual(png[:4], b"\x89PNG")
        self.assertIn("주봉", cap)
        self.assertIn("일목 구름", cap)
        self.assertIn("삼성전자", cap)

    def test_us_daily_no_ichimoku(self):
        png, cap = chart_for_symbol(_daily(), "US", "AAPL", "Apple Inc.")
        self.assertEqual(png[:4], b"\x89PNG")
        self.assertIn("일봉", cap)
        self.assertNotIn("일목", cap)

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            chart_for_symbol([(date(2024, 1, 1), 1, 1, 1, 1)], "US", "X")


if __name__ == "__main__":
    unittest.main()
