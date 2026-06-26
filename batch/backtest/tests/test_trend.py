"""저회전 추세 전략 검증: 진입/청산 방향 + 보유 유지(저회전) + 변동성 타게팅 사이징."""
import unittest
from decimal import Decimal

from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.models import BTick
from trading.strategy.trend import TrendStrategy, _ann_vol, _sma


def _ticks(prices):
    # 일봉 간격(86400s)으로 BTick 생성
    return [BTick("KRW-BTC", Decimal(str(p)), float(i) * 86400.0) for i, p in enumerate(prices)]


def _engine():
    return BacktestEngine(BacktestAccount(Decimal("1000000")), FillModel(), equity_sample_sec=86400.0)


class TestConstruction(unittest.TestCase):
    def test_short_ge_long_rejected(self):
        with self.assertRaises(ValueError):
            TrendStrategy(short=40, long=40)
        with self.assertRaises(ValueError):
            TrendStrategy(short=50, long=40)

    def test_warmup_covers_short(self):
        # warmup_bars는 short도 포함해 _sma(short) 슬라이스가 항상 충분
        s = TrendStrategy(short=10, long=40, vol_lookback=5)
        self.assertGreaterEqual(s.warmup_bars, s.short)
        self.assertGreaterEqual(s.warmup_bars, s.long)


class TestHelpers(unittest.TestCase):
    def test_sma_last_n(self):
        self.assertEqual(_sma([1, 2, 3, 4, 5], 3), 4.0)

    def test_ann_vol_none_when_insufficient(self):
        self.assertIsNone(_ann_vol([100, 101], 20, 365))

    def test_ann_vol_zero_for_flat(self):
        self.assertEqual(_ann_vol([100.0] * 21, 20, 365), 0.0)


class TestTrendSignals(unittest.TestCase):
    def _strat(self):
        # 회귀 무관 파라미터: 짧은 윈도우 + 레짐/변동성 게이트 사실상 해제(full invest)
        return TrendStrategy(short=3, long=5, vol_lookback=3, vol_target=99, regime_max_vol=999)

    def test_enters_on_uptrend(self):
        eng = _engine()
        prices = [100] * 5 + [101, 103, 106, 110, 115]   # 상승 → sma3>sma5
        eng.run(_ticks(prices), self._strat())
        self.assertGreater(eng.position_qty("KRW-BTC"), 0)

    def test_exits_on_downtrend(self):
        eng = _engine()
        prices = [100] * 5 + [101, 103, 106, 110, 115] + [112, 108, 103, 98, 92]  # 상승 후 하락
        eng.run(_ticks(prices), self._strat())
        self.assertEqual(eng.position_qty("KRW-BTC"), 0)

    def test_holds_through_uptrend_low_turnover(self):
        # 지속 상승 동안 단 1회 매수, 추가 매매 없음(저회전)
        eng = _engine()
        prices = [100] * 5 + [100 + 2 * i for i in range(1, 30)]
        eng.run(_ticks(prices), self._strat())
        self.assertGreater(eng.position_qty("KRW-BTC"), 0)
        self.assertEqual(len(eng.closed_trades), 0)   # 청산 없음 → 라운드트립 0

    def test_no_entry_during_warmup(self):
        eng = _engine()
        prices = [100, 102, 104, 106]   # warmup_bars=max(5,4)=5 미만
        eng.run(_ticks(prices), self._strat())
        self.assertEqual(eng.position_qty("KRW-BTC"), 0)


class TestVolTargeting(unittest.TestCase):
    def test_high_vol_reduces_weight(self):
        # 변동성 타게팅: 고변동일수록 진입 비중↓ → 매수 금액↓
        def invested(price_path):
            eng = _engine()
            s = TrendStrategy(short=3, long=5, vol_lookback=5, vol_target=0.5,
                              max_weight=1, regime_max_vol=999)
            eng.run(_ticks(price_path), s)
            return eng.account.cash  # 적게 살수록 현금 많이 남음

        calm = [100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5, 104, 104.5]
        wild = [100, 90, 115, 88, 120, 92, 125, 95, 130, 100]   # 동일 길이, 큰 변동
        # 둘 다 상승 추세지만 wild가 변동성↑ → 비중↓ → 현금 더 남음
        self.assertGreaterEqual(invested(wild), invested(calm))


class TestRebalance(unittest.TestCase):
    def test_band_zero_holds_without_intermediate_trades(self):
        # 기본(band=0): 추세 유지 중 매매 없음 → 청산 전까지 closed_trade 0
        eng = _engine()
        prices = [100] * 5 + [100 + 3 * i for i in range(1, 30)]  # 지속 상승
        eng.run(_ticks(prices), TrendStrategy(short=3, long=5, vol_lookback=5,
                                              vol_target=99, regime_max_vol=999, rebalance_band=0.0))
        self.assertEqual(len(eng.closed_trades), 0)

    def test_band_on_rebalances_when_vol_shifts(self):
        # band on: 저변동(전액)→고변동 구간에서 목표비중 하락 → REBAL 부분매도 발생
        eng = _engine()
        calm = [100 + i for i in range(1, 25)]                    # 완만 상승(저변동→큰 비중)
        wild = [124 + (8 if i % 2 else -6) + i for i in range(1, 20)]  # 상승 유지 + 큰 변동
        prices = [100] * 5 + calm + wild
        eng.run(_ticks(prices), TrendStrategy(short=3, long=5, vol_lookback=5, vol_target=0.5,
                                              max_weight=1, regime_max_vol=999, rebalance_band=0.3))
        reasons = [t.reason for t in eng.closed_trades]
        self.assertIn("REBAL", reasons)   # 변동성 상승 → 비중 축소 재조정


class TestRegimeFilter(unittest.TestCase):
    def test_extreme_vol_blocks_entry(self):
        eng = _engine()
        # 상승 추세지만 극단 변동성 → regime_max_vol 0.01 초과로 진입 차단
        s = TrendStrategy(short=3, long=5, vol_lookback=3, vol_target=99, regime_max_vol=0.01)
        prices = [100] * 5 + [101, 103, 106, 110, 115]
        eng.run(_ticks(prices), s)
        self.assertEqual(eng.position_qty("KRW-BTC"), 0)


if __name__ == "__main__":
    unittest.main()
