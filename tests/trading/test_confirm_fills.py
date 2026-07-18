"""잔고 diff 체결확인 검증 (confirm_fills·_fill_deadline — 가짜 시계·잔고 주입, DB/네트워크 없음).

side 방향 판정(매수 증가/매도 감소·전량 매도 소멸), 데드라인 폴링·조기종료,
폴 단위 오류 회복, 기본 데드라인(now+12초=현행 동등), KR 데드라인 경계(15:35 KST)를 검증한다.
"""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from trading.strategy.runners.stock_trade_common import confirm_fills
from trading.strategy.runners.stock_trade_once import _fill_deadline

_EPOCH = datetime(2026, 7, 7, 6, 0, tzinfo=timezone.utc)


class FakeClock:
    """sleep이 시간을 전진시키는 가짜 시계 — 실제 대기 없이 데드라인 루프를 검증."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return _EPOCH + timedelta(seconds=self.t)

    def sleep(self, sec):
        self.t += sec


def _balance_seq(snaps):
    """호출마다 스냅샷을 하나씩 소비(마지막은 반복). dict=잔고 {symbol: qty}, Exception=조회 실패."""
    state = list(snaps)
    calls = []

    def fn():
        s = state.pop(0) if len(state) > 1 else state[0]
        calls.append(s)
        if isinstance(s, Exception):
            raise s
        return {"positions": [{"symbol": k, "qty": v} for k, v in s.items()]}

    fn.calls = calls
    return fn


def _run(before, placed, snaps, deadline_sec=None, poll_sec=2.0):
    clock = FakeClock()
    fn = _balance_seq(snaps)
    deadline = _EPOCH + timedelta(seconds=deadline_sec) if deadline_sec is not None else None
    confirm_fills(fn, before, placed, deadline=deadline, poll_sec=poll_sec,
                  now_fn=clock.now, sleep_fn=clock.sleep)
    return fn, clock


class TestSideAware(unittest.TestCase):
    def test_buy_fill_increases(self):
        placed = [{"symbol": "005930", "side": "BUY", "accepted": True}]
        _run({}, placed, [{"005930": 3}], deadline_sec=30)
        self.assertTrue(placed[0]["filled"])
        self.assertEqual(placed[0]["filled_qty"], 3)

    def test_sell_full_fill_symbol_gone(self):
        # 전량 매도 체결 → 잔고에서 심볼 소멸(감소 방향으로 판정)
        placed = [{"symbol": "031980", "side": "SELL", "accepted": True}]
        _run({"031980": 5}, placed, [{}], deadline_sec=30)
        self.assertTrue(placed[0]["filled"])
        self.assertEqual(placed[0]["filled_qty"], 5)

    def test_sell_partial_fill(self):
        placed = [{"symbol": "031980", "side": "SELL", "accepted": True}]
        _run({"031980": 5}, placed, [{"031980": 2}], deadline_sec=30)
        self.assertTrue(placed[0]["filled"])
        self.assertEqual(placed[0]["filled_qty"], 3)

    def test_side_missing_defaults_buy(self):
        # 하위호환: side 없는 항목(US 등)은 매수 방향
        placed = [{"symbol": "AAPL", "accepted": True}]
        _run({}, placed, [{"AAPL": 1}], deadline_sec=30)
        self.assertTrue(placed[0]["filled"])

    def test_wrong_direction_not_filled(self):
        # 매도 주문인데 잔고 증가(방향 불일치) → 체결로 보지 않음
        placed = [{"symbol": "031980", "side": "SELL", "accepted": True}]
        _run({"031980": 5}, placed, [{"031980": 7}], deadline_sec=10)
        self.assertFalse(placed[0]["filled"])
        self.assertEqual(placed[0]["filled_qty"], 0)


class TestDeadlineLoop(unittest.TestCase):
    def test_early_exit_when_all_confirmed(self):
        # 접수 2건이 첫 폴에서 모두 확인 → 잔고 1회 조회 후 종료(데드라인까지 안 기다림)
        placed = [{"symbol": "A", "side": "BUY", "accepted": True},
                  {"symbol": "B", "side": "SELL", "accepted": True}]
        fn, clock = _run({"B": 4}, placed, [{"A": 2}], deadline_sec=600, poll_sec=5.0)
        self.assertEqual(len(fn.calls), 1)
        self.assertEqual(clock.t, 5.0)

    def test_polls_until_deadline_when_unfilled(self):
        # 미체결 지속 → 데드라인까지 폴링 후 filled=False (기본 12초/2초 = 현행 6회 동등)
        placed = [{"symbol": "A", "side": "BUY", "accepted": True}]
        fn, clock = _run({}, placed, [{}])          # deadline=None → now+12초
        self.assertFalse(placed[0]["filled"])
        self.assertEqual(len(fn.calls), 6)

    def test_late_fill_within_extended_deadline(self):
        # 동시호가 시나리오: 한동안 미체결 → 데드라인 안에서 늦게 체결 관찰
        placed = [{"symbol": "A", "side": "BUY", "accepted": True}]
        snaps = [{}] * 5 + [{"A": 2}]
        _run({}, placed, snaps, deadline_sec=600, poll_sec=5.0)
        self.assertTrue(placed[0]["filled"])
        self.assertEqual(placed[0]["filled_qty"], 2)

    def test_balance_error_recovers_next_poll(self):
        # 폴 1회 조회 실패는 건너뛰고 계속 → 다음 폴에서 체결 확인
        placed = [{"symbol": "A", "side": "BUY", "accepted": True}]
        _run({}, placed, [RuntimeError("KIS 5xx"), {"A": 2}], deadline_sec=60)
        self.assertTrue(placed[0]["filled"])


class TestFillDeadline(unittest.TestCase):
    _KST = ZoneInfo("Asia/Seoul")

    def test_auction_window_run_waits_until_1535(self):
        # 15:26 KST 실행(동시호가 접수) → 당일 15:35까지 대기
        now = datetime(2026, 7, 7, 15, 26, tzinfo=self._KST)
        self.assertEqual(_fill_deadline(now), now.replace(hour=15, minute=35, second=0))

    def test_late_run_gets_minimum_window(self):
        # 15:35 이후 비정상 지연 실행 → 최소창 now+60초
        now = datetime(2026, 7, 7, 15, 40, tzinfo=self._KST)
        self.assertEqual(_fill_deadline(now), now + timedelta(seconds=60))

    def test_utc_input_converted(self):
        # 컨테이너 TZ=UTC: 06:26 UTC = 15:26 KST → 당일 15:35 KST
        now = datetime(2026, 7, 7, 6, 26, tzinfo=timezone.utc)
        self.assertEqual(_fill_deadline(now),
                         datetime(2026, 7, 7, 15, 35, tzinfo=self._KST))


if __name__ == "__main__":
    unittest.main()
