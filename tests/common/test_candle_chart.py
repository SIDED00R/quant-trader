"""캔들차트 렌더 검증 (pillow — PNG 산출·엣지·비ASCII 가드)."""
import unittest
from datetime import date, timedelta

from common import ichimoku
from common.candle_chart import render_candle_chart


def _daily(n=140):
    out, p = [], 1000.0
    for i in range(n):
        d = date(2024, 1, 1) + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        p *= 1.01 if i % 7 < 4 else 0.99
        out.append((d, p, p * 1.02, p * 0.98, p * 1.005))
    return out


class TestRender(unittest.TestCase):
    def test_daily_png(self):
        bars = [(d, o, h, l, c, 0) for d, o, h, l, c in _daily()[-60:]]
        png = render_candle_chart(bars, "AAPL daily", lines=None)
        self.assertEqual(png[:4], b"\x89PNG")

    def test_weekly_with_ichimoku(self):
        wb = ichimoku.weekly_bars(_daily())
        png = render_candle_chart(wb, "005930 weekly", lines=ichimoku.ichimoku_lines(wb))
        self.assertEqual(png[:4], b"\x89PNG")

    def test_single_bar_no_crash(self):
        wb = ichimoku.weekly_bars(_daily())
        self.assertEqual(render_candle_chart(wb[:1], "one", lines=None)[:4], b"\x89PNG")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            render_candle_chart([], "empty")

    def test_non_ascii_title_guarded(self):
        bars = [(d, o, h, l, c, 0) for d, o, h, l, c in _daily()[-10:]]
        self.assertEqual(render_candle_chart(bars, "삼성전자 주봉", lines=None)[:4], b"\x89PNG")


if __name__ == "__main__":
    unittest.main()
