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
        self.assertIsNone(bot.parse_command("   "))                    # 공백만 — IndexError 아님


class TestHandleUpdate(unittest.TestCase):
    @patch("api.telegram_bot.TELEGRAM_ALLOWED_CHAT_IDS", set())
    @patch("api.telegram_bot.send_message")
    @patch("api.telegram_bot.handle_chart")
    def test_fail_closed_empty_allowlist(self, mock_handle, mock_send):
        # 빈 화이트리스트 = 전면 거부 — 처리·응답 없음
        bot._handle_update("t", {"message": {"chat": {"id": 1}, "text": "/차트 삼성전자"}}, IDX)
        mock_handle.assert_not_called()
        mock_send.assert_not_called()

    @patch("api.telegram_bot.TELEGRAM_ALLOWED_CHAT_IDS", {1})
    @patch("api.telegram_bot.send_photo")
    @patch("api.telegram_bot.send_message")
    @patch("api.telegram_bot.handle_chart", return_value="⚠️ 실패")
    def test_allowed_chat_processed(self, mock_handle, mock_send, mock_photo):
        bot._handle_update("t", {"message": {"chat": {"id": 1}, "text": "/차트 삼성전자"}}, IDX)
        mock_handle.assert_called_once()
        mock_send.assert_called_once()      # 에러 문자열 → sendMessage
        mock_photo.assert_not_called()


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
