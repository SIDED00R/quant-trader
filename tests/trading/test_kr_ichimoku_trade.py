"""KR 일목 페이퍼 매매 결정부 검증 (plan_trades 순수 함수 — DB/네트워크 없음).

청산(exit·신호소멸·stale) 무조건 · 매수 우선순위(돌파강도 내림차순) · 슬롯 캡 · 사이징(qty<1 스킵) ·
마커/계정 키 격리를 고정한다.
"""
import unittest
from datetime import date
from decimal import Decimal

from common.equity.equity_snapshot import ICHIMOKU_ACCOUNT
from trading.strategy.runners.kr_ichimoku_trade_once import MARKET_KEY, plan_trades

TODAY = date(2026, 7, 17)


def _sig(entry, exit_, brk, close):
    return {"entry": entry, "exit": exit_, "breakout_pct": brk, "close": close}


class TestPlanTrades(unittest.TestCase):
    def _base(self):
        signals = {
            "A": _sig(True, False, 0.10, 100),
            "B": _sig(True, False, 0.30, 200),   # 돌파강도 최고
            "C": _sig(True, False, 0.05, 50),
            "H": _sig(False, True, -0.02, 80),   # 보유·청산 신호
            "K": _sig(False, False, 0.01, 90),   # 보유·유지
        }
        held = {"H": 10, "K": 5}
        closes = {"A": 100.0, "B": 200.0, "C": 50.0, "H": 80.0, "K": 90.0}
        last_bar = {s: TODAY for s in signals}
        return signals, held, closes, last_bar

    def test_sell_exit_keep_and_buy_priority_with_cap(self):
        signals, held, closes, last_bar = self._base()
        p = plan_trades(signals, held, Decimal("1000000"), closes, last_bar, TODAY, max_positions=3)
        self.assertEqual([s for s, _, _ in p["sells"]], ["H"])       # exit 신호 → 청산
        self.assertEqual(p["keep"], {"K": 5})                        # 유지 신호 → 보유
        self.assertEqual([s for s, _, _ in p["buys"]], ["B", "A"])   # 돌파강도 desc, free=2(캡3-유지1) → C 제외
        self.assertTrue(all(q >= 1 for _, q, _ in p["buys"]))

    def test_stale_hold_forces_sell(self):
        signals, held, closes, last_bar = self._base()
        last_bar["K"] = date(2026, 6, 1)                             # 46일 무봉 → stale
        p = plan_trades(signals, held, Decimal("1000000"), closes, last_bar, TODAY, max_positions=3)
        self.assertIn("K", [s for s, _, _ in p["sells"]])
        self.assertNotIn("K", p["keep"])

    def test_qty_below_one_skipped(self):
        # 가격이 종목당 예산보다 크면 정수주<1 → 매수 스킵
        signals = {"X": _sig(True, False, 0.5, 1_000_000)}
        p = plan_trades(signals, {}, Decimal("100"), {"X": 1_000_000.0},
                        {"X": TODAY}, TODAY, max_positions=1)
        self.assertEqual(p["buys"], [])


class TestKeys(unittest.TestCase):
    def test_marker_and_account_isolated_from_ml(self):
        self.assertEqual(MARKET_KEY, "KR_ICHIMOKU")     # ML 'KR' 마커와 격리
        self.assertEqual(ICHIMOKU_ACCOUNT, "kr_ichimoku")


class TestSendEntryCharts(unittest.TestCase):
    def test_sends_per_buy_and_isolates_errors(self):
        import trading.strategy.runners.kr_ichimoku_trade_once as job
        from unittest import mock
        bars = [(date(2026, 7, 1), 1, 1, 1, 1)]
        buy_bars = [("005930", bars), ("000660", bars), ("035420", bars)]

        def fake_chart(b, market, symbol, name):
            if symbol == "000660":
                raise ValueError("render 실패")     # 한 종목 실패 → 격리, 나머지 계속
            return (b"png", f"{symbol} 캡션")

        with mock.patch("common.chart.symbol_chart.chart_for_symbol", side_effect=fake_chart), \
             mock.patch("common.marketdata.stock_names.fetch_all", return_value={"KR": [], "US": []}), \
             mock.patch.object(job.notify_telegram, "send_photo", return_value=True) as sp:
            job.send_entry_charts(buy_bars)

        self.assertEqual(sp.call_count, 2)              # 실패한 000660 제외 2건
        caps = [c.args[1] for c in sp.call_args_list]
        self.assertTrue(all(c.startswith("🟢 [KR 일목 매수]") for c in caps))

    def test_empty_is_noop(self):
        import trading.strategy.runners.kr_ichimoku_trade_once as job
        from unittest import mock
        with mock.patch.object(job.notify_telegram, "send_photo") as sp:
            job.send_entry_charts([])
        sp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
