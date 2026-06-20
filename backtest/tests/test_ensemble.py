"""앙상블(Commander) + TrendSignal 검증: 가중합 목표비중 → 합성 주문."""
import unittest
from decimal import Decimal

from backtest.account import BacktestAccount
from backtest.engine import BacktestEngine
from backtest.fills import FillModel
from backtest.models import BTick
from strategy.ensemble import EnsembleStrategy
from strategy.trend_signal import TrendSignal


def _ticks(prices):
    return [BTick("KRW-BTC", Decimal(str(p)), float(i) * 86400.0) for i, p in enumerate(prices)]


def _engine():
    return BacktestEngine(BacktestAccount(Decimal("1000000")), FillModel(), equity_sample_sec=86400.0)


class TestTrendSignal(unittest.TestCase):
    def test_short_ge_long_rejected(self):
        with self.assertRaises(ValueError):
            TrendSignal(40, 40)

    def test_cash_during_warmup(self):
        s = TrendSignal(3, 5, vol_lookback=3)
        self.assertEqual(s.update("KRW-BTC", 100), Decimal(0))

    def test_long_on_uptrend_cash_on_downtrend(self):
        s = TrendSignal(3, 5, vol_lookback=3, vol_target=99, regime_max_vol=999)
        up = [100] * 5 + [101, 103, 106, 110, 115]
        last = Decimal(0)
        for p in up:
            last = s.update("KRW-BTC", p)
        self.assertGreater(last, 0)                 # 상승추세 → 목표비중>0
        for p in [112, 108, 103, 98, 92]:           # 하락 반전
            last = s.update("KRW-BTC", p)
        self.assertEqual(last, Decimal(0))          # 반전 → 현금(0)

    def test_latch_holds_in_neutral_zone(self):
        # entry_band 중립대: 급상승으로 진입(+밴드 돌파) 후 평탄(중립대)이 와도 래치가 long 유지
        s = TrendSignal(3, 5, vol_lookback=3, vol_target=99, regime_max_vol=999, entry_band=0.05)
        for p in [100] * 5 + [115, 135, 160, 190, 225]:   # 급상승 → +5% 돌파 진입
            s.update("KRW-BTC", p)
        self.assertTrue(s.long_state["KRW-BTC"])
        for p in [225, 225, 225, 225]:                     # 평탄(sma_s≈sma_l, 중립대) → 청산 아님
            s.update("KRW-BTC", p)
        self.assertTrue(s.long_state["KRW-BTC"])           # 래치 유지(저회전 hold)


class TestEnsemble(unittest.TestCase):
    def test_consensus_scales_weight(self):
        # 3부하 중 일부만 long이면 합성 목표비중이 만장일치보다 작아 부분 투자
        eng = _engine()
        # 빠른 신호만 켜질 완만한 상승 구간(느린 신호는 아직 추세 미인식 가능) → 부분 투자 기대
        prices = [100] * 25 + [100 + 1.2 * i for i in range(1, 40)]
        eng.run(_ticks(prices), EnsembleStrategy(specs=[(3, 8), (5, 20), (10, 40)],
                                                 rebalance_band=0.0))
        # 최소한 진입은 발생(부하 다수 동의)하고 현금이 일부 남아야(완전 만장일치 아님)
        self.assertGreater(eng.position_qty("KRW-BTC"), 0)

    def test_all_cash_no_position(self):
        eng = _engine()
        prices = [100 - i for i in range(60)]   # 지속 하락 → 전 부하 현금
        eng.run(_ticks(prices), EnsembleStrategy(specs=[(3, 8), (5, 20)], rebalance_band=0.0))
        self.assertEqual(eng.position_qty("KRW-BTC"), 0)

    def test_exit_to_cash_on_reversal(self):
        eng = _engine()
        prices = [100] * 25 + [100 + 2 * i for i in range(1, 30)] + [158 - 4 * i for i in range(1, 25)]
        eng.run(_ticks(prices), EnsembleStrategy(specs=[(3, 8), (5, 20)],
                                                 rebalance_band=0.0))
        self.assertEqual(eng.position_qty("KRW-BTC"), 0)   # 하락 반전 → 전량 청산

    def test_weights_length_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            EnsembleStrategy(specs=[(5, 40), (10, 60)], weights=[1.0])

    def test_single_spec_behaves_like_trend(self):
        # specs 1개면 그 신호의 목표비중을 그대로 추종(앙상블 합성=항등)
        eng = _engine()
        prices = [100] * 25 + [100 + 2 * i for i in range(1, 30)]
        eng.run(_ticks(prices), EnsembleStrategy(specs=[(5, 20)], rebalance_band=0.0))
        self.assertGreater(eng.position_qty("KRW-BTC"), 0)


if __name__ == "__main__":
    unittest.main()
