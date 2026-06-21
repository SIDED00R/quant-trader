"""온디맨드 매매 잡 순수 결정부 검증 (plan_orders/plan_decisions — DB/Kafka/ClickHouse 연결 불필요)."""
import unittest
from datetime import date
from decimal import Decimal

from common.config import FEE_RATE
from strategy.trade_once import plan_decisions, plan_orders

_PX = Decimal("1000")
_CASH = Decimal("1000000")


def _snap(cash, positions, prices):
    return {"cash": cash, "positions": positions, "prices": prices}


def _a(target, signals=None, bar_date=None):
    return {"target": target, "bar_date": bar_date, "signals": signals or []}


class TestPlanOrders(unittest.TestCase):
    def test_empty_targets_no_orders(self):
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX})}
        self.assertEqual(plan_orders({}, snaps, 0.5), [])

    def test_buy_toward_target(self):
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX})}
        orders = plan_orders({"KRW-BTC": 0.5}, snaps, 0.5)
        self.assertEqual(len(orders), 1)
        acct, sym, side, qty = orders[0]
        self.assertEqual((acct, sym, side), ("a", "KRW-BTC", "BUY"))
        self.assertGreater(qty, 0)

    def test_missing_price_skipped(self):
        # 최신가 없는 종목은 체결 불가 → 주문 없음
        snaps = {"a": _snap(_CASH, {}, {})}
        self.assertEqual(plan_orders({"KRW-ETH": 0.5}, snaps, 0.5), [])

    def test_within_band_holds(self):
        # 보유=목표(50%) → 드리프트 0 < band → 주문 없음
        snaps = {"a": _snap(Decimal("500000"), {"KRW-BTC": Decimal("500")}, {"KRW-BTC": _PX})}
        self.assertEqual(plan_orders({"KRW-BTC": 0.5}, snaps, 0.5), [])

    def test_sequential_cash_prevents_overcommit(self):
        # 두 종목 각 50% → 순차 현금 차감으로 두 매수 합이 가용 현금을 넘지 않아야(정적 스냅샷이면 과투자)
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX, "KRW-ETH": _PX})}
        orders = plan_orders({"KRW-BTC": 0.5, "KRW-ETH": 0.5}, snaps, 0.5)
        self.assertEqual(len(orders), 2)
        self.assertTrue(all(side == "BUY" for _, _, side, _ in orders))
        spent = sum(qty * _PX * (Decimal(1) + FEE_RATE) for _, _, _, qty in orders)  # 수수료 포함 실비용
        self.assertLessEqual(spent, _CASH)        # 과투자 없음

    def test_multiple_accounts_independent(self):
        snaps = {
            "a": _snap(_CASH, {}, {"KRW-BTC": _PX}),
            "b": _snap(Decimal("500000"), {"KRW-BTC": Decimal("500")}, {"KRW-BTC": _PX}),  # 이미 목표
        }
        orders = plan_orders({"KRW-BTC": 0.5}, snaps, 0.5)
        accts = {o[0] for o in orders}
        self.assertIn("a", accts)        # a는 매수
        self.assertNotIn("b", accts)     # b는 밴드 내 유지


class TestPlanDecisions(unittest.TestCase):
    """결정 기록용 전수 산출 — 매매뿐 아니라 유지(HOLD)도 사유·근거와 함께 한 건씩 남긴다."""

    def test_no_entry_holds_with_reason_and_signals(self):
        sig = [{"load": "trend-5-40", "target": 0.0, "sma_s": 100.0, "sma_l": 120.0,
                "ann_vol": 0.5, "state": "CASH"}]
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX})}
        ds = plan_decisions({"KRW-BTC": _a(0.0, sig, date(2026, 6, 20))}, snaps, 0.5)
        self.assertEqual(len(ds), 1)
        d = ds[0]
        self.assertEqual((d["action"], d["quantity"], d["amount"]), ("HOLD", None, None))
        self.assertIn("추세 미진입", d["reason"])
        self.assertEqual(d["signals"], sig)            # 근거 통과
        self.assertEqual(d["bar_date"], date(2026, 6, 20))

    def test_buy_decision_has_amount(self):
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX})}
        d = plan_decisions({"KRW-BTC": _a(0.5)}, snaps, 0.5)[0]
        self.assertEqual(d["action"], "BUY")
        self.assertGreater(d["quantity"], 0)
        self.assertEqual(d["amount"], d["quantity"] * _PX)

    def test_missing_price_holds(self):
        snaps = {"a": _snap(_CASH, {}, {})}
        d = plan_decisions({"KRW-ETH": _a(0.5)}, snaps, 0.5)[0]
        self.assertEqual((d["action"], d["price"]), ("HOLD", None))
        self.assertIn("최신가 없음", d["reason"])

    def test_incomplete_signal_holds(self):
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX})}
        d = plan_decisions({"KRW-BTC": _a(None)}, snaps, 0.5)[0]
        self.assertEqual(d["action"], "HOLD")
        self.assertIn("불완전", d["reason"])

    def test_plan_orders_matches_decisions_trades(self):
        # plan_orders는 plan_decisions의 매매분만 — 두 종목(매수 1·유지 1)
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX, "KRW-ETH": _PX})}
        analysis = {"KRW-BTC": _a(0.5), "KRW-ETH": _a(0.0)}
        ds = plan_decisions(analysis, snaps, 0.5)
        trades = [d for d in ds if d["action"] in ("BUY", "SELL")]
        orders = plan_orders({"KRW-BTC": 0.5, "KRW-ETH": 0.0}, snaps, 0.5)
        self.assertEqual(len(ds), 2)
        self.assertEqual(len(trades), len(orders))     # 위임 일관성
        self.assertEqual(orders[0][1], "KRW-BTC")


if __name__ == "__main__":
    unittest.main()
