"""자산 차트 텔레그램 발송 검증 (_caption/_render_png/send_chart 격리 — 네트워크/DB 없음).

계약: 캡션=한글 명칭+통화 표기+수익률 · PNG 렌더=유효 시그니처 · send_chart는 어떤 실패도
밖으로 새지 않고 False(비치명 — trade_once 훅이라 매매 결과를 절대 못 깨뜨림).
"""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from common.chart import equity_chart_telegram as tg
from common.equity.equity_series import chart_rows

FX = [(date(2026, 7, 1), 1000.0), (date(2026, 7, 2), 1000.0)]


def _rows():
    markets = {
        "COIN": [(date(2026, 7, 1), 100.0, None), (date(2026, 7, 2), 110.0, None)],
        "KR": [(date(2026, 7, 1), 200.0, None), (date(2026, 7, 2), 210.0, None)],
        "US": [(date(2026, 7, 1), 1.0, None), (date(2026, 7, 2), 0.9, None)],
    }
    return chart_rows(markets, FX)


class TestCaption(unittest.TestCase):
    def test_korean_labels_money_and_returns(self):
        cap = tg._caption(_rows(), now=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc))
        self.assertIn("2026-07-14 01:00", cap)
        self.assertIn("코인 ₩110 (+10.0%)", cap)
        self.assertIn("미장 $1 (-10.0%)", cap)     # USD 표기
        self.assertIn("전체", cap)


class TestRenderPng(unittest.TestCase):
    def test_valid_png(self):
        png = tg._render_png(_rows())
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 5000)


class TestSendChartIsolation(unittest.TestCase):
    def test_load_failure_swallowed(self):
        with patch.object(tg, "_load_rows", side_effect=RuntimeError("pg down")):
            self.assertFalse(tg.send_chart())

    def test_no_data_skips_send(self):
        with patch.object(tg, "_load_rows", return_value=[]), \
             patch.object(tg.notify_telegram, "send_photo") as sp:
            self.assertFalse(tg.send_chart())
            sp.assert_not_called()

    def test_happy_path_sends_photo(self):
        with patch.object(tg, "_load_rows", return_value=_rows()), \
             patch.object(tg.notify_telegram, "send_photo", return_value=True) as sp:
            self.assertTrue(tg.send_chart())
            png, caption = sp.call_args.args
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertIn("자산 추이", caption)


if __name__ == "__main__":
    unittest.main()
