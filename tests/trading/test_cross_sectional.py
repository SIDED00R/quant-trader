"""횡단면 전략 + 봉버퍼링 엔진 통합 검증 (합성 분봉, 네트워크/DB 불필요).

엔진(BacktestEngine)·계좌·체결모델을 실제로 쓰고, 데이터소스가 (symbol,ts)별 1봉을 시간순 yield하는 것을 모사.
"""
import unittest
from decimal import Decimal

from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.models import BTick
from trading.strategy.plugins.cross_sectional import XSMomentumStrategy, XSReversalStrategy
from trading.strategy.core.rebalance import bar_key, decide


def _xs_ticks(prices_by_bar, bar_sec=60.0):
    """[{sym: price, ...}, ...] 봉별 → BTick 스트림(봉당 ts=i*bar_sec, 봉 내 심볼순)."""
    return [BTick(sym, Decimal(str(p)), float(i) * bar_sec)
            for i, bar in enumerate(prices_by_bar) for sym, p in bar.items()]


# A=승자(단조 상승), B=패자(단조 하락), C=평탄 — 미국주식 티커(영문)로 취급
_PRICES = [
    {"A": 100, "B": 100, "C": 100},
    {"A": 110, "B": 90, "C": 100},
    {"A": 120, "B": 80, "C": 100},
    {"A": 130, "B": 70, "C": 100},
    {"A": 140, "B": 60, "C": 100},
    {"A": 150, "B": 50, "C": 100},
]


def _run(strategy_cls, prices=_PRICES, **kw):
    acct = BacktestAccount(Decimal("1000000"))
    eng = BacktestEngine(acct, FillModel(), equity_sample_sec=60.0)
    params = dict(bar_min=1, lookback=2, top_n=1, max_weight=Decimal("1"), rebalance_band=0.0)
    params.update(kw)
    eng.run(_xs_ticks(prices), strategy_cls(**params))
    return eng, acct


class TestCrossSectional(unittest.TestCase):
    def test_reversal_holds_loser(self):
        eng, acct = _run(XSReversalStrategy)
        self.assertGreater(acct.qty("B"), 0)              # 패자 B 보유
        self.assertEqual(acct.qty("A"), Decimal(0))
        self.assertEqual(acct.qty("C"), Decimal(0))

    def test_momentum_holds_winner(self):
        eng, acct = _run(XSMomentumStrategy)
        self.assertGreater(acct.qty("A"), 0)              # 승자 A 보유
        self.assertEqual(acct.qty("B"), Decimal(0))

    def test_long_or_cash_invariant(self):
        eng, acct = _run(XSReversalStrategy)
        for sym in ("A", "B", "C"):
            self.assertGreaterEqual(acct.qty(sym), 0)     # 공매도 없음
        self.assertGreaterEqual(acct.cash, 0)

    def test_warmup_no_trades(self):
        # lookback(=2) 미충족(봉 2개) → 거래 0, 현금 불변
        eng, acct = _run(XSReversalStrategy, prices=_PRICES[:2])
        self.assertEqual(len(eng.closed_trades), 0)
        self.assertEqual(acct.cash, Decimal("1000000"))

    def test_stale_symbol_liquidated(self):
        # 보유 종목(B)이 특정 봉에서 미관측되면 long-or-cash 원칙상 청산돼야 함(리뷰 fix #2)
        prices = [
            {"A": 100, "B": 100, "C": 100},
            {"A": 110, "B": 90, "C": 100},
            {"A": 120, "B": 80, "C": 100},   # bar3 진입: B(패자) 매수
            {"A": 130, "B": 70, "C": 100},   # B 유지
            {"A": 140, "C": 80},             # B 미관측 + C가 패자
            {"A": 150, "C": 85},             # bar5 진입 시 _on_bar_close(bar4): B 청산·C 매수
        ]
        eng, acct = _run(XSReversalStrategy, prices=prices)
        self.assertEqual(acct.qty("B"), Decimal(0))   # 미관측 보유분 청산됨
        self.assertGreater(acct.qty("C"), 0)

    def test_kr_sell_tax_reused(self):
        # 국내주식(005930)이 횡단면 매도될 때 엔진 fills.tax 경유 → sell_tax>0 (비용모델 재사용 확인)
        prices = [
            {"005930": 100, "000660": 100},
            {"005930": 120, "000660": 100},   # bar1까지 005930 승자
            {"005930": 120, "000660": 150},   # bar2: 000660 승자로 역전
            {"005930": 120, "000660": 160},   # bar3 진입 시 005930 매도→000660 매수
        ]
        eng, acct = _run(XSMomentumStrategy, prices=prices, lookback=1)
        kr = [t for t in eng.closed_trades if t.symbol == "005930"]
        self.assertTrue(kr, "005930이 매도되어 청산 트레이드가 있어야 함")
        self.assertGreater(kr[0].sell_tax, 0)             # 국내 매도세 재사용
        self.assertEqual(acct.qty("005930") % 1, 0)       # 주식 정수 주(엔진 _adjust_qty 재사용)


class TestRebalancePure(unittest.TestCase):
    def test_bar_key_float_wobble(self):
        self.assertEqual(bar_key(60.0, 60.0), 1)
        self.assertEqual(bar_key(60.0000001, 60.0), 1)    # 미세 흔들림 같은 봉
        self.assertEqual(bar_key(119.9, 60.0), 1)
        self.assertEqual(bar_key(120.0, 60.0), 2)

    def test_decide_target_zero_sells_all(self):
        self.assertEqual(decide(Decimal("10"), Decimal("100"), Decimal("0"), Decimal("1000"), 0.0, 0.3),
                         ("SELL", Decimal("10")))

    def test_decide_band_holds(self):
        # 보유가치(500)≈목표(1000×0.5=500) → 밴드 이내 → 유지(None)
        self.assertIsNone(decide(Decimal("5"), Decimal("100"), Decimal("500"), Decimal("1000"), 0.5, 0.3))


if __name__ == "__main__":
    unittest.main()
