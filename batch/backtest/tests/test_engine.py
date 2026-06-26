"""엔진+SMA 전략 통합 검증 (합성 데이터, ClickHouse 불필요).

또한 strategy.sma → strategy.sma_trader import가 DB 연결 없이 안전한지 검증한다.
"""
import unittest
from decimal import Decimal
from unittest import mock

import trading.strategy.sma as sma_mod
from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.models import BTick
from trading.strategy.sma import SMAStrategy

_TEST_WARMUP = 30  # 1.5단계 가드 재튜닝 전 조건으로 고정(엔진/전략 로직 검증은 튜닝 기본값과 분리)


def _rising_ticks(n=120, step=Decimal("0.001"), base=Decimal("100"), dt=1.0):
    """1초 간격 단조 상승(0.1%/틱) — 워밍업 경과 후 BUY 진입 → 익절(TAKE) 발생."""
    return [BTick("KRW-BTC", base * (Decimal(1) + step * i), float(i) * dt) for i in range(n)]


class TestEngine(unittest.TestCase):
    def setUp(self):
        # SMA 전략 가드를 캡처 시점 값으로 고정(워밍업 짧게·수수료필터 off·데드크로스 on)
        for attr, val in (("STRATEGY_WARMUP_SEC", _TEST_WARMUP),
                          ("STRATEGY_MIN_EDGE_PCT", Decimal("0")),
                          ("STRATEGY_DEADCROSS_EXIT", True)):
            p = mock.patch.object(sma_mod, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def _run(self, ticks, initial="1000000"):
        acct = BacktestAccount(Decimal(initial))
        eng = BacktestEngine(acct, FillModel(), equity_sample_sec=10.0)
        eng.run(ticks, SMAStrategy())
        return eng

    def test_uptrend_produces_take_profit_trades(self):
        ticks = _rising_ticks()
        eng = self._run(ticks)

        self.assertEqual(eng.n_bars, len(ticks))
        self.assertGreaterEqual(len(eng.closed_trades), 1, "상승장에서 최소 1회 매매가 있어야 함")
        first = eng.closed_trades[0]
        self.assertEqual(first.reason, "TAKE", "단조 상승이면 청산 사유는 익절")
        self.assertGreater(first.pnl, 0)
        # 모든 청산은 익절, 모든 진입은 워밍업 이후
        for t in eng.closed_trades:
            self.assertEqual(t.reason, "TAKE")
            self.assertGreaterEqual(t.entry_ts, float(_TEST_WARMUP))

    def test_no_entry_before_warmup(self):
        # 워밍업 구간 안에서만 끝나는 짧은 상승 → 진입 없음
        short = _rising_ticks(n=_TEST_WARMUP - 1)
        eng = self._run(short)
        self.assertEqual(len(eng.closed_trades), 0)
        self.assertEqual(eng.account.qty("KRW-BTC"), Decimal("0"))

    def test_flat_market_no_trades(self):
        flat = [BTick("KRW-BTC", Decimal("100"), float(i)) for i in range(120)]
        eng = self._run(flat)
        self.assertEqual(len(eng.closed_trades), 0)  # 이격 0 → 신호 없음


if __name__ == "__main__":
    unittest.main()
