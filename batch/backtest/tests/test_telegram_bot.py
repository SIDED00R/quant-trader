"""텔레그램 /차트 봇 검증 (명령 파싱·미해석/조회실패 한글 에러 — 네트워크 없이 mock)."""
import unittest
from unittest.mock import patch

from api import telegram_bot as bot
from common.stock_names import build_index

IDX = build_index({"KR": [("005930", "삼성전자")], "US": [("AAPL", "Apple Inc.")]})


class TestParseCommand(unittest.TestCase):
    def test_chart_variants(self):
        self.assertEqual(bot.parse_command("/차트 삼성전자"), ("chart", "삼성전자"))
        self.assertEqual(bot.parse_command("/chart 005930"), ("chart", "005930"))
        self.assertEqual(bot.parse_command("/chart@mybot AAPL"), ("chart", "AAPL"))

    def test_help_and_none(self):
        self.assertEqual(bot.parse_command("/차트"), ("help", ""))     # 인자 없음 → 도움말
        self.assertEqual(bot.parse_command("/help"), ("help", ""))
        self.assertEqual(bot.parse_command("/start"), ("help", ""))
        self.assertIsNone(bot.parse_command("안녕하세요"))
        self.assertIsNone(bot.parse_command(""))


class TestHandleChart(unittest.TestCase):
    def test_unresolved_returns_korean_error(self):
        res = bot.handle_chart("없는종목xyz", IDX)
        self.assertIsInstance(res, str)
        self.assertTrue(res.startswith("❓"))

    @patch("api.telegram_bot.fetch_daily", return_value=[])
    def test_no_data_returns_korean_error(self, _):
        res = bot.handle_chart("삼성전자", IDX)      # 해석은 되나 시세 0행
        self.assertIsInstance(res, str)
        self.assertTrue(res.startswith("⚠️"))


if __name__ == "__main__":
    unittest.main()
