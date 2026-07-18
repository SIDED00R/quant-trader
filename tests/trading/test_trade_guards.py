"""매매 잡 가드 회귀 테스트 (단일 책임: skip 반환 형태·커버리지 기준일 선택)."""
import unittest

import pandas as pd

from batch.ml.stock_score import _latest_covered
from trading.strategy.core.notify_messages import stock_message
from trading.strategy.runners.stock_trade_common import skip_result


class TestSkipResult(unittest.TestCase):
    def test_skip_result_shape(self):
        r = skip_result("사유")
        for key in ("bar", "cash", "targets", "buys", "sells", "placed", "skipped"):
            self.assertIn(key, r)
        # us_trade_once.main의 요약 print 포맷이 예외 없이 채워지는지
        summary = (f"bar={r['bar']} cash={r['cash']:,.2f} "
                   f"targets={len(r['targets'])} buys={len(r['buys'])} sells={len(r['sells'])} live=True")
        self.assertIn("targets=0", summary)
        self.assertIn("매매하지 않음", stock_message("US 주식", r, live=True))


class TestLatestCovered(unittest.TestCase):
    def _feats(self, sizes: list) -> pd.DataFrame:
        dates = pd.date_range("2026-06-01", periods=len(sizes), freq="D")
        rows = []
        for d, n in zip(dates, sizes):
            for i in range(n):
                rows.append({"date": d, "symbol": f"S{i}"})
        return pd.DataFrame(rows)

    def test_thin_last_day_falls_back_to_prior(self):
        sizes = [20] * 14 + [2]     # 최근 15일 중 마지막 날만 얇음
        feats = self._feats(sizes)
        dates = sorted(feats["date"].unique())
        self.assertEqual(_latest_covered(feats), dates[-2])   # 직전(완전한) 날짜 선택

    def test_full_coverage_picks_max_date(self):
        sizes = [20] * 15           # 전 날짜 완전
        feats = self._feats(sizes)
        dates = sorted(feats["date"].unique())
        self.assertEqual(_latest_covered(feats), dates[-1])


if __name__ == "__main__":
    unittest.main()
