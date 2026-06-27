"""주식 체결/계좌 모델 검증 (7단계 #4) — 자산군·시장시간·매도세·정수단위, 코인 무영향.

합성 데이터, 네트워크 불필요. 코인 경로가 기존과 동일함을 회귀로 고정한다.
"""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from common.market_hours import asset_class, is_coin, is_market_open, is_stock, periods_per_year

UTC = timezone.utc


class TestAssetClass(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(asset_class("KRW-BTC"), "COIN")
        self.assertEqual(asset_class("005930"), "STOCK_KR")
        self.assertEqual(asset_class("AAPL"), "STOCK_US")

    def test_is_coin_is_stock(self):
        self.assertTrue(is_coin("KRW-ETH"))
        self.assertFalse(is_coin("005930"))
        self.assertTrue(is_stock("005930"))
        self.assertTrue(is_stock("AAPL"))
        self.assertFalse(is_stock("KRW-BTC"))


class TestMarketHours(unittest.TestCase):
    # 2026-06-29는 월요일, 2026-06-27은 토요일
    def test_coin_always_open(self):
        # 토요일 새벽이어도 코인은 항상 True
        self.assertTrue(is_market_open("KRW-BTC", datetime(2026, 6, 27, 1, 0, tzinfo=UTC)))

    def test_kr_stock_weekday_session(self):
        # 평일(월) 10:00 KST = 01:00 UTC → open
        self.assertTrue(is_market_open("005930", datetime(2026, 6, 29, 1, 0, tzinfo=UTC)))
        # 08:59 KST(=전일 23:59 UTC) → closed(장전)
        self.assertFalse(is_market_open("005930", datetime(2026, 6, 28, 23, 59, tzinfo=UTC)))
        # 15:30 KST = 06:30 UTC → 경계 포함 open
        self.assertTrue(is_market_open("005930", datetime(2026, 6, 29, 6, 30, tzinfo=UTC)))
        # 15:31 KST = 06:31 UTC → closed(장후)
        self.assertFalse(is_market_open("005930", datetime(2026, 6, 29, 6, 31, tzinfo=UTC)))

    def test_kr_stock_weekend_closed(self):
        # 토요일 10:00 KST → 휴장
        self.assertFalse(is_market_open("005930", datetime(2026, 6, 27, 1, 0, tzinfo=UTC)))

    def test_us_summer_session_edt(self):
        # 2026-06-29(월) 여름 EDT(UTC-4): 09:30 ET = 13:30 UTC
        self.assertTrue(is_market_open("AAPL", datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))    # 10:00 EDT open
        self.assertFalse(is_market_open("AAPL", datetime(2026, 6, 29, 13, 0, tzinfo=UTC)))   # 09:00 EDT 장전
        self.assertTrue(is_market_open("AAPL", datetime(2026, 6, 29, 20, 0, tzinfo=UTC)))    # 16:00 EDT 경계 open
        self.assertFalse(is_market_open("AAPL", datetime(2026, 6, 29, 20, 1, tzinfo=UTC)))   # 16:01 EDT 장후

    def test_us_winter_est_and_dst(self):
        # 2026-01-05(월) 겨울 EST(UTC-5): 09:30 ET = 14:30 UTC
        self.assertTrue(is_market_open("AAPL", datetime(2026, 1, 5, 15, 0, tzinfo=UTC)))     # 10:00 EST open
        # 동일 13:45 UTC가 여름엔 open(09:45 EDT)·겨울엔 closed(08:45 EST) → DST 반영
        self.assertTrue(is_market_open("AAPL", datetime(2026, 6, 29, 13, 45, tzinfo=UTC)))
        self.assertFalse(is_market_open("AAPL", datetime(2026, 1, 5, 13, 45, tzinfo=UTC)))

    def test_us_weekend_closed(self):
        self.assertFalse(is_market_open("AAPL", datetime(2026, 6, 27, 14, 0, tzinfo=UTC)))   # 토요일


class TestPeriodsPerYear(unittest.TestCase):
    def test_coin_unchanged_24_7(self):
        self.assertEqual(periods_per_year("KRW-BTC", 86400), 365.0)        # 일봉
        self.assertEqual(periods_per_year("KRW-BTC", 60), 365.0 * 1440)    # 분봉 = 525,600

    def test_stock_trading_days_and_session(self):
        self.assertEqual(periods_per_year("005930", 86400), 252.0)         # 주식 일봉
        self.assertEqual(periods_per_year("005930", 60), 252.0 * 390)      # 주식 분봉 = 98,280(6.5h)
        self.assertEqual(periods_per_year("AAPL", 60), 252.0 * 390)        # 미국주식 동일 세션


class TestFillTax(unittest.TestCase):
    def setUp(self):
        self.fm = FillModel()  # fee_rate=0.0005, STOCK_SELL_TAX_RATE=0.0020

    def test_coin_and_us_no_tax(self):
        self.assertEqual(self.fm.tax("KRW-BTC", Decimal("100"), Decimal("10")), Decimal("0"))
        self.assertEqual(self.fm.tax("AAPL", Decimal("185"), Decimal("10")), Decimal("0"))

    def test_kr_stock_sell_tax(self):
        # 800,000 × 0.0020 = 1,600.0000
        self.assertEqual(self.fm.tax("005930", Decimal("80000"), Decimal("10")), Decimal("1600.0000"))

    def test_fee_is_asset_agnostic(self):
        # fee()는 자산군 무관 동일(회귀): 800,000 × 0.0005 = 400.0000
        self.assertEqual(self.fm.fee(Decimal("80000"), Decimal("10")), Decimal("400.0000"))


class TestAccountSellTax(unittest.TestCase):
    def test_kr_stock_sell_includes_tax(self):
        acct = BacktestAccount(Decimal("10000000"))
        acct.apply_buy("005930", Decimal("70000"), Decimal("10"), Decimal("350"), ts=0.0)  # 매수세 없음
        # 매도 800,000: fee=400, tax=1,600 → proceeds=798,000. avg=700,350/10=70,035 → cost=700,350
        trade = acct.apply_sell("005930", Decimal("80000"), Decimal("10"),
                                Decimal("400"), ts=1.0, tax=Decimal("1600"))
        self.assertEqual(trade.sell_fee, Decimal("400"))
        self.assertEqual(trade.sell_tax, Decimal("1600"))
        self.assertEqual(trade.pnl, Decimal("97650"))  # 798,000 − 700,350

    def test_coin_sell_tax_default_zero_regression(self):
        # tax 미전달 → 기존 코인 수학 불변(proceeds=price*qty-fee), sell_tax=0
        acct = BacktestAccount(Decimal("10000000"))
        acct.apply_buy("KRW-BTC", Decimal("100"), Decimal("10"), Decimal("5"), ts=0.0)
        trade = acct.apply_sell("KRW-BTC", Decimal("120"), Decimal("10"), Decimal("6"), ts=1.0)
        self.assertEqual(trade.sell_tax, Decimal("0"))
        # proceeds=1200-6=1194, cost=(1000+5)/10*10=1005 → pnl=189
        self.assertEqual(trade.pnl, Decimal("189"))


class TestEngineIntegerUnits(unittest.TestCase):
    def _eng(self):
        return BacktestEngine(BacktestAccount(Decimal("10000000")), FillModel())

    def test_stock_qty_rounded_down(self):
        eng = self._eng()
        eng.last_price["005930"] = Decimal("70000")
        self.assertTrue(eng.buy("005930", Decimal("10.7"), ts=0.0))
        self.assertEqual(eng.account.qty("005930"), Decimal("10"))  # 정수 내림

    def test_stock_sub_one_share_skipped(self):
        eng = self._eng()
        eng.last_price["005930"] = Decimal("70000")
        before = eng.account.cash
        self.assertFalse(eng.buy("005930", Decimal("0.9"), ts=0.0))  # <1주 → skip
        self.assertEqual(eng.account.cash, before)                    # 상태 불변
        self.assertEqual(eng.account.qty("005930"), Decimal("0"))

    def test_coin_qty_fractional_unchanged(self):
        eng = self._eng()
        eng.last_price["KRW-BTC"] = Decimal("100000000")
        self.assertTrue(eng.buy("KRW-BTC", Decimal("0.00012345"), ts=0.0))
        self.assertEqual(eng.account.qty("KRW-BTC"), Decimal("0.00012345"))  # 소수 그대로

    def test_engine_sell_applies_kr_tax(self):
        eng = self._eng()
        eng.last_price["005930"] = Decimal("70000")
        eng.buy("005930", Decimal("10"), ts=0.0)
        eng.last_price["005930"] = Decimal("80000")
        self.assertTrue(eng.sell("005930", Decimal("10"), "TAKE", ts=1.0))
        self.assertEqual(eng.closed_trades[-1].sell_tax, Decimal("1600.0000"))


if __name__ == "__main__":
    unittest.main()
