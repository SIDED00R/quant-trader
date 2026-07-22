"""KIS 주문 추격 검증 (place_and_chase — 이중체결 방지가 핵심 계약).

_poll_fill을 패치해 시도별 체결량을 스크립트하고, _held_qty로 기준선·취소후 재확인 값을 제어한다.
소비 모듈 attr을 패치(from-import 바인딩): kc.place_overseas_order 등. DB/네트워크/실시계 무접촉.
"""
import unittest
from unittest.mock import patch

from common.broker import kis_chase as kc


def _order(odno):
    return {"output": {"ODNO": odno}}


class TestPlaceAndChase(unittest.TestCase):
    def test_us_partial_then_cancel_reorder_full(self):
        with patch.object(kc, "_held_qty", side_effect=[0.0, 3.0]), \
             patch.object(kc, "_poll_fill", side_effect=[3, 10]), \
             patch.object(kc, "place_overseas_order", side_effect=[_order("111"), _order("222")]) as po, \
             patch.object(kc, "cancel_overseas_order") as cancel, \
             patch.object(kc, "price_and_exchange", return_value=(100.0, "NASD")):
            r = kc.place_and_chase("US", "AAPL", "BUY", 10, ref_price=100.0, exchange="NASD", confirm_window=5)
        self.assertEqual(r["status"], "FILLED")
        self.assertEqual(r["filled_qty"], 10)
        self.assertEqual(len(r["attempts"]), 2)
        self.assertEqual(r["attempts"][1]["qty"], 7)                 # 부분체결 3 → 잔여 7 재주문
        self.assertEqual(r["attempts"][1]["limit"], round(100.0 * 1.03, 2))   # 2차 버퍼 에스컬레이션
        cancel.assert_called_once_with("AAPL", "111", 7, "NASD")
        self.assertEqual(po.call_count, 2)

    def test_cancel_failure_blocks_reorder(self):
        # 취소 실패 = 취소 미확인 → 재주문 금지(이중체결 방지 핵심)
        with patch.object(kc, "_held_qty", side_effect=[0.0, 3.0]), \
             patch.object(kc, "_poll_fill", side_effect=[3]), \
             patch.object(kc, "place_overseas_order", side_effect=[_order("111")]) as po, \
             patch.object(kc, "cancel_overseas_order", side_effect=RuntimeError("취소 거부")), \
             patch.object(kc, "price_and_exchange", return_value=(100.0, "NASD")):
            r = kc.place_and_chase("US", "AAPL", "BUY", 10, ref_price=100.0, exchange="NASD", confirm_window=5)
        self.assertEqual(po.call_count, 1)                          # 재주문 없음
        self.assertEqual(r["status"], "PARTIAL")
        self.assertEqual(r["filled_qty"], 3)
        self.assertIn("cancel", r["attempts"][0])

    def test_recheck_failure_after_cancel_blocks_reorder(self):
        # 취소 성공했으나 잔고 재확인 실패 → 막판 체결 미확인이면 재주문 금지
        with patch.object(kc, "_held_qty", side_effect=[0.0, RuntimeError("잔고 5xx")]), \
             patch.object(kc, "_poll_fill", side_effect=[3]), \
             patch.object(kc, "place_overseas_order", side_effect=[_order("111")]) as po, \
             patch.object(kc, "cancel_overseas_order"), \
             patch.object(kc, "price_and_exchange", return_value=(100.0, "NASD")):
            r = kc.place_and_chase("US", "AAPL", "BUY", 10, ref_price=100.0, exchange="NASD", confirm_window=5)
        self.assertEqual(po.call_count, 1)                          # 재주문 없음
        self.assertIn("recheck", r["attempts"][0])
        self.assertEqual(r["status"], "PARTIAL")

    def test_baseline_query_failure_propagates_without_order(self):
        # 주문 전 기준선 조회 실패 = 아직 주문 없음 → 예외 전파(주문 미발행)
        with patch.object(kc, "_held_qty", side_effect=RuntimeError("잔고 조회 실패")), \
             patch.object(kc, "place_overseas_order") as po, \
             patch.object(kc, "price_and_exchange", return_value=(100.0, "NASD")):
            with self.assertRaises(RuntimeError):
                kc.place_and_chase("US", "AAPL", "BUY", 10, ref_price=100.0, exchange="NASD", confirm_window=5)
        po.assert_not_called()

    def test_first_order_rejected_returns_rejected(self):
        with patch.object(kc, "_held_qty", side_effect=[0.0]), \
             patch.object(kc, "place_overseas_order", side_effect=RuntimeError("장외·잔고부족")), \
             patch.object(kc, "cancel_overseas_order") as cancel, \
             patch.object(kc, "price_and_exchange", return_value=(100.0, "NASD")):
            r = kc.place_and_chase("US", "AAPL", "BUY", 10, ref_price=100.0, exchange="NASD", confirm_window=5)
        self.assertEqual(r["status"], "REJECTED")
        self.assertEqual(r["filled_qty"], 0)
        cancel.assert_not_called()

    def test_kr_market_single_attempt_no_cancel(self):
        # KR 시장가는 1회 시도 후 종료(취소·재주문 없음)
        with patch.object(kc, "_held_qty", side_effect=[0.0]), \
             patch.object(kc, "_poll_fill", side_effect=[0]), \
             patch.object(kc, "place_domestic_order", return_value=_order("K1")) as pd, \
             patch.object(kc, "cancel_overseas_order") as cancel:
            r = kc.place_and_chase("KR", "005930", "BUY", 10, confirm_window=5)
        pd.assert_called_once()
        cancel.assert_not_called()
        self.assertEqual(r["status"], "UNFILLED")
        self.assertEqual(len(r["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
