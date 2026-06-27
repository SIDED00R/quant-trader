"""인트라데이 세션 전략(ORB·모멘텀) 검증 (합성 분봉, 네트워크/DB 불필요).

KRX 정규장(09:00–15:30 KST) 가정. 005930(국내주식)으로 세션 경계·개장레인지·마감 청산을 검증.
"""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from batch.backtest.account import BacktestAccount
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.models import BTick
from trading.strategy.intraday import IntradayMomentumStrategy, ORBStrategy

_KST = timezone(timedelta(hours=9))


def _ts(h, m, day=29):
    return datetime(2026, 6, day, h, m, tzinfo=_KST).timestamp()   # 2026-06-29=월


def _run(strat, ticks):
    acct = BacktestAccount(Decimal("1000000"))
    eng = BacktestEngine(acct, FillModel(), equity_sample_sec=60.0)
    eng.run(ticks, strat)
    return eng, acct


class TestORB(unittest.TestCase):
    def test_breakout_enters_then_flat_at_close(self):
        # opening_bars=2: 09:00/09:01 레인지(100), 09:02 돌파(110) 매수, 15:30 마감 봉 청산(오버나잇 미보유)
        ticks = [
            BTick("005930", Decimal("100"), _ts(9, 0)),
            BTick("005930", Decimal("100"), _ts(9, 1)),
            BTick("005930", Decimal("110"), _ts(9, 2)),
            BTick("005930", Decimal("115"), _ts(15, 30)),
        ]
        eng, acct = _run(ORBStrategy(bar_min=1, opening_bars=2), ticks)
        self.assertEqual(acct.qty("005930"), Decimal(0))
        self.assertGreaterEqual(len(eng.closed_trades), 1)
        self.assertEqual(eng.closed_trades[-1].reason, "SESSION_CLOSE")

    def test_no_breakout_no_entry(self):
        ticks = [
            BTick("005930", Decimal("100"), _ts(9, 0)),
            BTick("005930", Decimal("100"), _ts(9, 1)),
            BTick("005930", Decimal("99"), _ts(9, 2)),
            BTick("005930", Decimal("98"), _ts(9, 3)),
        ]
        eng, acct = _run(ORBStrategy(bar_min=1, opening_bars=2), ticks)
        self.assertEqual(len(eng.closed_trades), 0)
        self.assertEqual(acct.qty("005930"), Decimal(0))


class TestIntradayMomentum(unittest.TestCase):
    def test_positive_open_return_enters(self):
        # 개장 2봉 수익률 +5%(>0) → 매수, 마감 청산
        ticks = [
            BTick("005930", Decimal("100"), _ts(9, 0)),
            BTick("005930", Decimal("105"), _ts(9, 1)),
            BTick("005930", Decimal("106"), _ts(9, 2)),
            BTick("005930", Decimal("108"), _ts(15, 30)),
        ]
        eng, acct = _run(IntradayMomentumStrategy(bar_min=1, opening_bars=2, threshold=Decimal("0")), ticks)
        self.assertEqual(acct.qty("005930"), Decimal(0))
        self.assertGreaterEqual(len(eng.closed_trades), 1)

    def test_negative_open_return_no_entry(self):
        ticks = [
            BTick("005930", Decimal("100"), _ts(9, 0)),
            BTick("005930", Decimal("95"), _ts(9, 1)),
            BTick("005930", Decimal("96"), _ts(9, 2)),
        ]
        eng, acct = _run(IntradayMomentumStrategy(bar_min=1, opening_bars=2, threshold=Decimal("0")), ticks)
        self.assertEqual(len(eng.closed_trades), 0)


class TestSessionBoundary(unittest.TestCase):
    def test_overnight_flatten_safety(self):
        # 마감 봉 없이 다음 거래일 첫 봉이 오면 안전망으로 전일 보유 청산(SESSION_END)
        ticks = [
            BTick("005930", Decimal("100"), _ts(9, 0, day=29)),
            BTick("005930", Decimal("100"), _ts(9, 1, day=29)),
            BTick("005930", Decimal("110"), _ts(9, 2, day=29)),   # day29 진입
            BTick("005930", Decimal("100"), _ts(9, 0, day=30)),   # day30 새 세션 첫 봉 → 청산
            BTick("005930", Decimal("100"), _ts(9, 1, day=30)),
        ]
        eng, acct = _run(ORBStrategy(bar_min=1, opening_bars=2), ticks)
        self.assertTrue(any(t.reason == "SESSION_END" for t in eng.closed_trades))


if __name__ == "__main__":
    unittest.main()
