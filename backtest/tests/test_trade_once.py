"""온디맨드 매매 잡 순수 결정부 검증 (plan_orders/plan_decisions — DB/Kafka/ClickHouse 불필요)."""
import unittest
from decimal import Decimal

from common.config import FEE_RATE
from strategy.trade_once import plan_decisions, plan_orders

_PX = Decimal("1000")
_CASH = Decimal("1000000")


def _snap(cash, positions, prices):
    return {"cash": cash, "positions": positions, "prices": prices}


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
    def test_records_every_symbol_including_no_trade(self):
        # 한 종목은 밴드 내(유지), 한 종목은 매수 → 둘 다 1건씩 기록(매매 안 한 것도)
        snaps = {"a": _snap(Decimal("500000"), {"KRW-BTC": Decimal("500")},
                            {"KRW-BTC": _PX, "KRW-ETH": _PX})}
        rows = plan_decisions({"KRW-BTC": 0.5, "KRW-ETH": 0.0}, snaps, 0.5)
        self.assertEqual(len(rows), 2)
        by_sym = {sym: d for _, sym, _, d in rows}
        self.assertEqual(by_sym["KRW-BTC"].decision, "HOLD")   # 보유=목표 → 유지(기록됨)
        self.assertEqual(by_sym["KRW-ETH"].decision, "HOLD")   # 목표0·보유없음 → 유지(기록됨)

    def test_missing_price_recorded_as_skip(self):
        # 최신가 없어도 주문엔 없지만 결정 기록엔 SKIP 1건 남는다
        snaps = {"a": _snap(_CASH, {}, {})}
        rows = plan_decisions({"KRW-ETH": 0.5}, snaps, 0.5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3].decision, "SKIP")
        self.assertEqual(plan_orders({"KRW-ETH": 0.5}, snaps, 0.5), [])

    def test_plan_orders_is_buy_sell_subset(self):
        snaps = {"a": _snap(_CASH, {}, {"KRW-BTC": _PX})}
        rows = plan_decisions({"KRW-BTC": 0.5}, snaps, 0.5)
        orders = plan_orders({"KRW-BTC": 0.5}, snaps, 0.5)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0][2], "BUY")
        self.assertEqual([d.decision for _, _, _, d in rows], ["BUY"])


if __name__ == "__main__":
    unittest.main()
