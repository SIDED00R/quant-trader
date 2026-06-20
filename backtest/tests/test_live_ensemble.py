"""라이브 앙상블 신호 워커 검증 (순수 상태기 — Kafka/ClickHouse 불필요)."""
import unittest
from decimal import Decimal

from strategy.live_ensemble import LiveEnsemble, utc_day


def _hist(closes):
    # closes 리스트 → [(date, Decimal), ...] (2026-01-01부터 1일씩 증가)
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    return [(base + timedelta(days=i), Decimal(str(c))) for i, c in enumerate(closes)]


class TestUtcDay(unittest.TestCase):
    def test_parses_tz_aware(self):
        self.assertEqual(str(utc_day("2026-06-20T07:00:00+00:00")), "2026-06-20")

    def test_naive_treated_as_utc(self):
        self.assertEqual(str(utc_day("2026-06-20T23:30:00")), "2026-06-20")


class TestPrime(unittest.TestCase):
    def test_prime_warms_and_returns_initial_signal(self):
        le = LiveEnsemble(["KRW-BTC"])
        # 충분한 상승 일봉 히스토리(앙상블 워밍업 충족) → 초기 목표비중>0
        closes = [100 + i for i in range(130)]
        out = le.prime({"KRW-BTC": _hist(closes)})
        self.assertEqual(len(out), 1)
        sym, day, t = out[0]
        self.assertEqual(sym, "KRW-BTC")
        self.assertGreater(t, 0)                 # 상승추세 → 보유 목표
        self.assertIn("KRW-BTC", le.cur_day)     # 상태 세팅됨

    def test_prime_empty_history_no_signal(self):
        le = LiveEnsemble(["KRW-BTC"])
        self.assertEqual(le.prime({"KRW-BTC": []}), [])


class TestOnTick(unittest.TestCase):
    def _primed(self):
        le = LiveEnsemble(["KRW-BTC"])
        le.prime({"KRW-BTC": _hist([100 + i for i in range(130)])})
        return le

    def test_first_tick_no_signal_sets_day(self):
        le = LiveEnsemble(["KRW-BTC"])   # 미프라임 — 첫 틱은 당일 시작만
        self.assertIsNone(le.on_tick("KRW-BTC", Decimal("100"), "2026-06-20T01:00:00+00:00"))
        self.assertEqual(str(le.cur_day["KRW-BTC"]), "2026-06-20")

    def test_same_day_updates_close_no_signal(self):
        le = self._primed()
        le.on_tick("KRW-BTC", Decimal("230"), "2026-06-20T01:00:00+00:00")  # 첫 라이브 틱(당일)
        self.assertIsNone(le.on_tick("KRW-BTC", Decimal("240"), "2026-06-20T20:00:00+00:00"))
        self.assertEqual(le.day_close["KRW-BTC"], Decimal("240"))

    def test_new_day_emits_signal_with_prev_close(self):
        le = self._primed()
        le.on_tick("KRW-BTC", Decimal("250"), "2026-06-20T01:00:00+00:00")
        le.on_tick("KRW-BTC", Decimal("260"), "2026-06-20T23:00:00+00:00")  # 6/20 종가=260
        sig = le.on_tick("KRW-BTC", Decimal("261"), "2026-06-21T00:30:00+00:00")  # 새 일자 → 6/20 마감
        self.assertIsNotNone(sig)
        self.assertEqual(sig[0], "KRW-BTC")
        self.assertEqual(str(sig[1]), "2026-06-20")   # 신호의 봉 = 마감된 직전 일자
        self.assertEqual(str(le.cur_day["KRW-BTC"]), "2026-06-21")

    def test_non_universe_symbol_ignored(self):
        le = self._primed()
        self.assertIsNone(le.on_tick("KRW-DOGE", Decimal("1"), "2026-06-21T00:00:00+00:00"))

    def test_backward_day_ignored(self):
        le = self._primed()
        le.on_tick("KRW-BTC", Decimal("250"), "2026-06-20T10:00:00+00:00")
        self.assertIsNone(le.on_tick("KRW-BTC", Decimal("9"), "2026-06-19T10:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
