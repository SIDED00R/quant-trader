"""일목균형표 지표 검증 (순수 함수 — DB/네트워크 없음).

주봉 리샘플(ISO주 경계·drop_week_of 무룩어헤드) · 라인 수치(전환/기준/선행A/B 시프트) ·
최신 신호(진입/청산 조건·exit_rule 분기)를 하드코딩 값으로 검증한다.
"""
import unittest
from datetime import date

from common.ichimoku import ichimoku_lines, latest_signal, weekly_bars

# 라인 테스트용 (date, o, h, l, c, v) — 라인은 h(idx2)·l(idx3)만 쓴다.
def _bars(highs, lows, closes=None):
    n = len(highs)
    closes = closes or [0.0] * n
    return [(date(2024, 1, 1 + i), 0.0, float(highs[i]), float(lows[i]), float(closes[i]), 0) for i in range(n)]


class TestWeeklyBars(unittest.TestCase):
    W1 = [
        (date(2024, 1, 1), 100, 110, 95, 105, 10),
        (date(2024, 1, 2), 105, 112, 100, 108, 20),
        (date(2024, 1, 3), 108, 115, 102, 111, 30),
        (date(2024, 1, 4), 111, 113, 90, 109, 40),
        (date(2024, 1, 5), 109, 114, 106, 112, 50),
    ]
    W2 = [
        (date(2024, 1, 8), 112, 120, 111, 118, 5),
        (date(2024, 1, 9), 118, 122, 115, 120, 5),
    ]

    def test_ohlc_aggregation(self):
        bars = weekly_bars(self.W1 + self.W2)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0], (date(2024, 1, 5), 100, 115, 90, 112, 150))  # open=Mon, high=max, low=min, close=Fri, vol=합
        self.assertEqual(bars[1], (date(2024, 1, 9), 112, 122, 111, 120, 10))

    def test_drop_week_of_removes_current_week(self):
        bars = weekly_bars(self.W1 + self.W2, drop_week_of=date(2024, 1, 8))  # 2주차(진행 중) 제거
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0][0], date(2024, 1, 5))

    def test_no_lookahead(self):
        # 2주차 부분봉을 붙여도 'today가 2주차'면 결과 == 1주차만(진행 주 미반영)
        full = weekly_bars(self.W1 + self.W2, drop_week_of=date(2024, 1, 9))
        only1 = weekly_bars(self.W1)
        self.assertEqual(full, only1)


class TestIchimokuLines(unittest.TestCase):
    def setUp(self):
        self.lines = ichimoku_lines(_bars([10, 12, 11, 15, 14, 13], [5, 6, 4, 7, 8, 6]),
                                    tenkan=2, kijun=3, senkou_b=4, disp=1)

    def test_tenkan_kijun(self):
        self.assertIsNone(self.lines["tenkan"][0])          # 이력 부족
        self.assertEqual(self.lines["tenkan"][1], 8.5)      # (max(10,12)+min(5,6))/2
        self.assertEqual(self.lines["tenkan"][2], 8.0)
        self.assertIsNone(self.lines["kijun"][1])
        self.assertEqual(self.lines["kijun"][2], 8.0)       # (max(10,12,11)+min(5,6,4))/2

    def test_senkou_shift(self):
        # span은 raw를 +disp 시프트(과거 데이터로 그린 구름 — 무룩어헤드)
        self.assertEqual(self.lines["span_a"][3], 8.0)      # a_raw[2]=(8.0+8.0)/2
        self.assertEqual(self.lines["span_a"][5], 10.25)    # a_raw[4]=(11.0+9.5)/2
        self.assertIsNone(self.lines["span_b"][3])          # b_raw[2] 미정의(win4)
        self.assertEqual(self.lines["span_b"][5], 9.5)      # b_raw[4]


class TestLatestSignal(unittest.TestCase):
    KW = dict(tenkan=2, kijun=3, senkou_b=4, disp=1)

    def test_none_when_short_or_incomplete(self):
        self.assertIsNone(latest_signal(_bars([10], [5]), **self.KW))
        self.assertIsNone(latest_signal(_bars([10, 11, 12], [5, 6, 7]), **self.KW))  # 구름 미정의

    def test_entry_true(self):
        bars = _bars([10, 11, 12, 13, 20, 14], [9, 10, 11, 12, 13, 13], closes=[0, 0, 0, 0, 0, 25])
        sig = latest_signal(bars, exit_rule="tk_cross", **self.KW)
        self.assertTrue(sig["entry"])       # close 25 > cloud_top AND 전환>기준
        self.assertFalse(sig["exit"])
        self.assertGreater(sig["breakout_pct"], 0)

    def test_exit_rule_divergence(self):
        # 종가는 구름 위(cloud 청산 미발동)지만 전환<기준(tk 청산 발동) — 두 규칙 분기
        bars = _bars([10, 10, 10, 30, 20, 18], [9, 9, 9, 10, 10, 9], closes=[0, 0, 0, 0, 0, 25])
        self.assertTrue(latest_signal(bars, exit_rule="tk_cross", **self.KW)["exit"])
        self.assertFalse(latest_signal(bars, exit_rule="cloud", **self.KW)["exit"])
        self.assertFalse(latest_signal(bars, exit_rule="tk_cross", **self.KW)["entry"])  # 전환<기준이라 진입 아님


if __name__ == "__main__":
    unittest.main()
