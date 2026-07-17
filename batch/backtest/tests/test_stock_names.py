"""종목명 사전 검증 (build_index·resolve 우선순위·티커 폴백 — 순수 함수, 네트워크 없음)."""
import unittest

from common.stock_names import build_index, resolve

NAMES = {
    "KR": [("005930", "삼성전자"), ("000660", "SK하이닉스")],
    "US": [("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corp")],
}


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.idx = build_index(NAMES)

    def test_exact_symbol(self):
        self.assertEqual(resolve(self.idx, "005930"), ("KR", "005930", "삼성전자"))
        self.assertEqual(resolve(self.idx, "aapl"), ("US", "AAPL", "Apple Inc."))

    def test_exact_and_prefix_name(self):
        self.assertEqual(resolve(self.idx, "삼성전자"), ("KR", "005930", "삼성전자"))
        self.assertEqual(resolve(self.idx, "Apple")[1], "AAPL")     # 유일 prefix

    def test_miss_returns_none(self):
        self.assertIsNone(resolve(self.idx, "없는종목명xyz"))

    def test_ticker_fallback_without_dict(self):
        empty = build_index({"KR": [], "US": []})
        self.assertEqual(resolve(empty, "005930"), ("KR", "005930", "005930"))
        self.assertEqual(resolve(empty, "AAPL"), ("US", "AAPL", "AAPL"))
        self.assertIsNone(resolve(empty, "삼성전자"))                 # 사전 없고 티커도 아님


if __name__ == "__main__":
    unittest.main()
