"""DeliveryWatchdog 검증 (가짜 clock 주입 — 시간/네트워크 의존 없음).

핵심 계약: 한산한 시장(pending=0)은 절대 발화하지 않고, 에러 배달은 리셋이 아니며,
성공 배달만 윈도우·pending을 리셋한다 (2026-07-18 프로듀서 침묵 생존 사고 회귀 고정).
"""
import unittest

from common.kafka_watchdog import DeliveryWatchdog


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestDeliveryWatchdog(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.wd = DeliveryWatchdog(stall_sec=180, clock=self.clock)

    def test_quiet_market_never_stalls(self):
        # 틱 자체가 없으면(pending=0) 아무리 지나도 미발화 — 오탐 방지 핵심
        self.clock.t += 100000
        self.assertFalse(self.wd.stalled())

    def test_normal_delivery_not_stalled(self):
        self.wd.record_produce()
        self.clock.t += 1
        self.wd.record_delivery(None)
        self.clock.t += 179
        self.assertFalse(self.wd.stalled())

    def test_stalls_when_no_success_past_threshold(self):
        self.wd.record_produce()
        self.clock.t += 181
        self.assertTrue(self.wd.stalled())

    def test_error_delivery_does_not_reset(self):
        # _MSG_TIMED_OUT 홍수 시나리오 — 에러 배달은 생존 증거가 아니므로 발화해야 한다
        self.wd.record_produce()
        self.clock.t += 100
        self.wd.record_delivery(RuntimeError("_MSG_TIMED_OUT"))
        self.clock.t += 100
        self.assertTrue(self.wd.stalled())

    def test_success_resets_window_and_pending(self):
        self.wd.record_produce()
        self.clock.t += 100
        self.wd.record_delivery(None)
        self.assertEqual(self.wd.pending, 0)
        self.wd.record_produce()
        self.clock.t += 179
        self.assertFalse(self.wd.stalled())
        self.clock.t += 2
        self.assertTrue(self.wd.stalled())

    def test_startup_grace(self):
        # 기동 직후 첫 produce — 성공 배달이 아직 없어도 stall_sec 이내면 미발화
        self.clock.t += 10
        self.wd.record_produce()
        self.clock.t += 100
        self.assertFalse(self.wd.stalled())


if __name__ == "__main__":
    unittest.main()
