"""관심종목 검색 랭킹 검증 (merge_search_results — 순수 함수, DB 없음)."""
import unittest

from api.routes.watchlist import merge_search_results

ROWS = [("005930", "KR", "삼성전자"), ("000660", "KR", "SK하이닉스"), ("AAPL", "US", "Apple Inc.")]


class TestMergeSearch(unittest.TestCase):
    def test_exact_name_and_watched_flag(self):
        res = merge_search_results(ROWS, {("KR", "005930")}, "삼성전자")
        self.assertEqual(res[0]["symbol"], "005930")
        self.assertTrue(res[0]["watched"])

    def test_exact_symbol_first(self):
        self.assertEqual(merge_search_results(ROWS, set(), "AAPL")[0]["symbol"], "AAPL")

    def test_prefix(self):
        self.assertEqual(merge_search_results(ROWS, set(), "SK")[0]["symbol"], "000660")

    def test_ticker_synthetic_when_dict_miss(self):
        kr = merge_search_results([], set(), "012345")
        self.assertEqual((kr[0]["market"], kr[0]["symbol"], kr[0]["name"]), ("KR", "012345", None))
        us = merge_search_results([], set(), "tsla")
        self.assertEqual((us[0]["market"], us[0]["symbol"]), ("US", "TSLA"))
        self.assertEqual(merge_search_results([], set(), "없는이름"), [])   # 티커도 아님

    def test_cap_20(self):
        rows = [(f"{i:06d}", "KR", f"종목{i}") for i in range(50)]
        self.assertLessEqual(len(merge_search_results(rows, set(), "종목")), 20)


if __name__ == "__main__":
    unittest.main()
