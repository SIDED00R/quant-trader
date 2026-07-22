"""kis_cancel 단위 테스트 — 해외 미체결 주문 취소 body 계약·TR 토글·예외 전파 검증.

cancel_overseas_order는 kis_order.post_order(hashkey 서명 POST)를 재사용해 취소를
"주문"으로 보낸다(RVSE_CNCL_DVSN_CD=02). 체결 추격(kis_chase)이 예외 처리를 전제하므로
post_order 예외는 가공 없이 그대로 전파돼야 한다.
"""
import unittest
from unittest import mock

import common.broker.kis_cancel as kc


class _Base(unittest.TestCase):
    """공통 패치 — 계좌 분해·TR 토글을 고정하고 post_order 호출만 기록."""

    def setUp(self):
        p1 = mock.patch.object(kc, "split_account", return_value=("12345678", "01"))
        p1.start()
        self.addCleanup(p1.stop)
        p2 = mock.patch.object(kc, "post_order", return_value={"rt_cd": "0"})
        self.post_order = p2.start()
        self.addCleanup(p2.stop)


class TestCancelBodyContract(_Base):
    def test_cancel_body_fields(self):
        """취소 body — 취소구분코드/원주문번호/종목/수량(str)/거래소/단가=0/경로 검증."""
        with mock.patch.object(kc, "_tr", side_effect=lambda real_id: "V" + real_id[1:]):
            result = kc.cancel_overseas_order("AAPL", "0000000123", 5, "NASD")

        self.assertEqual(result, {"rt_cd": "0"})
        args, kwargs = self.post_order.call_args
        path, tr, body = args
        self.assertEqual(path, "/uapi/overseas-stock/v1/trading/order-rvsecncl")
        self.assertEqual(tr, "VTTT1004U")
        self.assertEqual(body["RVSE_CNCL_DVSN_CD"], "02")
        self.assertEqual(body["ORGN_ODNO"], "0000000123")
        self.assertEqual(body["PDNO"], "AAPL")
        self.assertEqual(body["ORD_QTY"], "5")
        self.assertIsInstance(body["ORD_QTY"], str)
        self.assertEqual(body["OVRS_EXCG_CD"], "NASD")
        self.assertEqual(body["OVRS_ORD_UNPR"], "0")
        self.assertEqual(body["CANO"], "12345678")
        self.assertEqual(body["ACNT_PRDT_CD"], "01")


class TestTrToggle(_Base):
    def test_mock_mode_toggles_v_prefix(self):
        """모의(V) 토글 — _tr 반환값이 그대로 post_order tr 인자로 전달."""
        with mock.patch.object(kc, "_tr", side_effect=lambda real_id: "V" + real_id[1:]):
            kc.cancel_overseas_order("AAPL", "1", 1, "NASD")
        _, tr, _ = self.post_order.call_args.args
        self.assertEqual(tr, "VTTT1004U")

    def test_real_mode_keeps_original_tr(self):
        """실전(T) 토글 — _tr이 원본 TR ID를 그대로 반환하면 그대로 전달."""
        with mock.patch.object(kc, "_tr", side_effect=lambda real_id: real_id):
            kc.cancel_overseas_order("AAPL", "1", 1, "NASD")
        _, tr, _ = self.post_order.call_args.args
        self.assertEqual(tr, "TTTT1004U")


class TestExceptionPropagation(_Base):
    def test_post_order_exception_propagates(self):
        """post_order 예외(예: EGW00201 소진)가 가공 없이 그대로 전파(콜러 kis_chase가 처리)."""
        self.post_order.side_effect = RuntimeError("KIS 주문 실패(VTTT1004U): EGW00201 초당 거래건수 초과")
        with mock.patch.object(kc, "_tr", return_value="VTTT1004U"), \
                self.assertRaises(RuntimeError) as cm:
            kc.cancel_overseas_order("AAPL", "1", 1, "NASD")
        self.assertIn("EGW00201", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
