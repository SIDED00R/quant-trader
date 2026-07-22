"""rate_limit 단위 테스트 — 토큰버킷 페이싱·first-wins·기본 한도표 (2026-07-20 EGW00201 사고 근원).

FakeClock으로 실시각을 대체(sleep이 시간을 전진시켜 실제 대기 없음). _limiters 레지스트리는
매 테스트 clear해 격리한다. 핵심 계약: ① rate 페이싱(연속 요청 시 대기) ② capacity 기본=버스트
없음 ③ first-wins(같은 키 재호출 시 최초 rate 유지 — 모의/실전 그룹 분리 강제 근거)
④ 미등록 키 KeyError ⑤ kis:rest 기본 rate 회귀 고정(사고 재발 방지 핀).
"""
import unittest
from unittest import mock

import common.rate_limit as rate_limit


class FakeClock:
    """sleep이 시간을 전진시키는 가짜 단조시계 — 실제 대기 없이 페이싱을 검증."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, sec):
        self.sleeps.append(sec)
        self.t += sec


class _Base(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        p1 = mock.patch.object(rate_limit.time, "monotonic", side_effect=self.clock.monotonic)
        p1.start()
        self.addCleanup(p1.stop)
        p2 = mock.patch.object(rate_limit.time, "sleep", side_effect=self.clock.sleep)
        p2.start()
        self.addCleanup(p2.stop)
        p3 = mock.patch.dict(rate_limit._limiters, clear=True)
        p3.start()
        self.addCleanup(p3.stop)


class TestPacing(_Base):
    def test_second_acquire_waits_half_second_at_2rps(self):
        """rate=2/s — 버스트 소진 직후 둘째 호출은 ~0.5초 대기한다."""
        rate_limit.acquire("t", "g1", rate=2.0)
        rate_limit.acquire("t", "g1", rate=2.0)
        self.assertEqual(self.clock.sleeps, [0.5])

    def test_default_capacity_no_burst(self):
        """capacity 미지정 시 기본 1.0 = 버스트 없는 균등 페이싱."""
        bucket = rate_limit.limiter("t", "g2", rate=5.0)
        self.assertEqual(bucket.capacity, 1.0)


class TestFirstWins(_Base):
    def test_first_rate_locked_ignores_later_rate(self):
        """같은 (provider,group) 키 재호출 — 다른 rate를 줘도 최초 버킷·rate가 유지된다."""
        b1 = rate_limit.limiter("t", "g3", rate=2.0)
        b2 = rate_limit.limiter("t", "g3", rate=100.0)
        self.assertIs(b1, b2)
        self.assertEqual(b2.rate, 2.0)


class TestUnregisteredKey(_Base):
    def test_unregistered_key_raises_keyerror(self):
        """기본표에 없고 rate도 안 준 키 — KeyError(무음 폴백 방지)."""
        with self.assertRaises(KeyError):
            rate_limit.acquire("unknown_provider", "unknown_group")


class TestDefaultRatesRegression(_Base):
    def test_kis_rest_default_rate_pinned(self):
        """kis:rest 기본 rate=2 회귀 고정(2026-07-20 EGW00201 사고 — 모의서버 실측 한도)."""
        self.assertEqual(rate_limit._DEFAULT_RATES["kis:rest"], 2)


if __name__ == "__main__":
    unittest.main()
