"""kis_order 단위 테스트 — EGW00201 한정 재시도·4xx/5xx body 로깅·hashkey 멱등 재시도.

2026-07-20 사고 회귀 고정: 모의서버가 초당 한도 초과를 HTTP 500 + msg_cd=EGW00201로
반환하는데, 예외 메시지에 msg_cd/msg1이 남아야 하고(진단), EGW00201(게이트웨이 거부=
미접수 확실)만 백오프 재시도해야 한다(그 외 5xx/전송오류는 비멱등 보호로 즉시 예외).
"""
import unittest
from unittest import mock

import httpx

import common.broker.kis_order as ko

_THROTTLE_BODY = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
_OK_BODY = {"rt_cd": "0", "msg_cd": "APBK0013", "msg1": "정상", "output": {"ODNO": "12345"}}


class _Resp:
    """가짜 httpx 응답. payload=None이면 json() 실패(비JSON body)."""

    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "http://test")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=httpx.Response(self.status_code, request=req))

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _fake_post(responses, calls):
    """미리 준비한 응답을 순차 반환하는 가짜 httpx.post. 응답이 예외면 raise."""
    seq = list(responses)

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return post


class _Base(unittest.TestCase):
    """공통 패치 — 페이싱/대기/인증을 무력화하고 호출 횟수만 기록."""

    def setUp(self):
        self.calls = []
        self.sleeps = []
        for target, repl in (
            ("acquire", lambda *a, **k: None),
            ("_headers", lambda tr: {"tr_id": tr}),
        ):
            p = mock.patch.object(ko, target, side_effect=repl)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ko.time, "sleep", side_effect=self.sleeps.append)
        p.start()
        self.addCleanup(p.stop)

    def _patch_post(self, responses):
        p = mock.patch.object(ko.httpx, "post", side_effect=_fake_post(responses, self.calls))
        p.start()
        self.addCleanup(p.stop)

    def _patch_hashkey(self):
        p = mock.patch.object(ko, "_hashkey", return_value="H")
        p.start()
        self.addCleanup(p.stop)


class TestPostOrderThrottle(_Base):
    def test_throttle_500_retried_then_success(self):
        """EGW00201 500 → 백오프 재시도 → 성공."""
        self._patch_hashkey()
        self._patch_post([_Resp(500, _THROTTLE_BODY), _Resp(200, _OK_BODY)])
        b = ko.post_order("/order", "VTTC0801U", {"PDNO": "042000"})
        self.assertEqual(b["output"]["ODNO"], "12345")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [1.0])

    def test_throttle_exhausted_raises_with_msg(self):
        """EGW00201 연속 → 재시도 소진 예외에 msg_cd/msg1/소진 표기."""
        self._patch_hashkey()
        self._patch_post([_Resp(500, _THROTTLE_BODY)] * 3)
        with self.assertRaises(RuntimeError) as cm:
            ko.post_order("/order", "VTTC0801U", {})
        self.assertIn("EGW00201", str(cm.exception))
        self.assertIn("초당 거래건수", str(cm.exception))
        self.assertIn("재시도 소진", str(cm.exception))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_throttle_200_variant_retried(self):
        """EGW00201이 HTTP 200으로 와도(변형) 재시도."""
        self._patch_hashkey()
        self._patch_post([_Resp(200, _THROTTLE_BODY), _Resp(200, _OK_BODY)])
        b = ko.post_order("/order", "VTTC0802U", {})
        self.assertEqual(str(b.get("rt_cd")), "0")
        self.assertEqual(len(self.calls), 2)

    def test_other_500_no_retry_body_logged(self):
        """비스로틀 500 — 즉시 예외(재시도·sleep 없음), body msg_cd/msg1 포함(회귀 고정)."""
        self._patch_hashkey()
        self._patch_post([_Resp(500, {"msg_cd": "EGW00001", "msg1": "서버 오류"})])
        with self.assertRaises(RuntimeError) as cm:
            ko.post_order("/order", "VTTC0801U", {})
        self.assertIn("500", str(cm.exception))
        self.assertIn("EGW00001", str(cm.exception))
        self.assertIn("서버 오류", str(cm.exception))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_500_non_json_body(self):
        """비JSON 500 body — 크래시 없이 예외, text 스니펫 포함."""
        self._patch_hashkey()
        self._patch_post([_Resp(500, None, text="Internal Server Error")])
        with self.assertRaises(RuntimeError) as cm:
            ko.post_order("/order", "VTTC0801U", {})
        self.assertIn("Internal Server Error", str(cm.exception))
        self.assertEqual(len(self.calls), 1)

    def test_200_rtcd_error_no_retry(self):
        """일반 거부(200 + rt_cd!=0) — 기존 동작 유지: 무재시도, msg 포함."""
        self._patch_hashkey()
        self._patch_post([_Resp(200, {"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "매수여력 부족"})])
        with self.assertRaises(RuntimeError) as cm:
            ko.post_order("/order", "VTTC0802U", {})
        self.assertIn("APBK0919", str(cm.exception))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_transport_error_propagates(self):
        """전송오류(타임아웃 등) — 접수 여부 불명 → 재시도 없이 전파(비멱등 보호)."""
        self._patch_hashkey()
        self._patch_post([httpx.ConnectTimeout("timeout")])
        with self.assertRaises(httpx.TransportError):
            ko.post_order("/order", "VTTC0801U", {})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_hashkey_computed_once_and_header_set(self):
        """hashkey는 1회만 계산되고 재시도에 재사용된다."""
        hk = mock.patch.object(ko, "_hashkey", return_value="H")
        m = hk.start()
        self.addCleanup(hk.stop)
        self._patch_post([_Resp(500, _THROTTLE_BODY), _Resp(200, _OK_BODY)])
        ko.post_order("/order", "VTTC0801U", {"PDNO": "139130"})
        self.assertEqual(m.call_count, 1)


class TestHashkeyRetry(_Base):
    def test_hashkey_5xx_retried_then_success(self):
        """hashkey는 멱등 — 5xx 재시도 후 성공."""
        self._patch_post([_Resp(500, _THROTTLE_BODY), _Resp(200, {"HASH": "abc"})])
        self.assertEqual(ko._hashkey({}), "abc")
        self.assertEqual(len(self.calls), 2)

    def test_hashkey_transport_retried(self):
        """hashkey 전송오류도 재시도(멱등)."""
        self._patch_post([httpx.ConnectTimeout("t"), _Resp(200, {"HASH": "abc"})])
        self.assertEqual(ko._hashkey({}), "abc")
        self.assertEqual(len(self.calls), 2)

    def test_hashkey_exhausted_raises(self):
        self._patch_post([_Resp(503, None, text="unavailable")] * 3)
        with self.assertRaises(RuntimeError) as cm:
            ko._hashkey({})
        self.assertIn("재시도 소진", str(cm.exception))
        self.assertEqual(len(self.calls), 3)

    def test_hashkey_4xx_immediate(self):
        """4xx(인증 오류 등) — 재시도 무의미, 즉시 실패."""
        self._patch_post([_Resp(403, {"msg_cd": "EGW00103", "msg1": "권한 없음"})])
        with self.assertRaises(httpx.HTTPStatusError):
            ko._hashkey({})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
