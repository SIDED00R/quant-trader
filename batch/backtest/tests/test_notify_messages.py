"""텔레그램 알림 문안 조립 검증 (notify_messages — 순수 함수, I/O 없음)."""
import unittest

from trading.strategy.notify_messages import coin_message, error_message, stock_message


def _r(**kw):
    base = {"bar": "2026-07-06", "cash": 10_000_000.0, "targets": ["005930", "000660"],
            "buys": ["005930"], "sells": [], "placed": [], "skipped": None}
    base.update(kw)
    return base


class TestStockMessage(unittest.TestCase):
    def test_skip_reason_shown(self):
        m = stock_message("KR 주식", _r(skipped="이미 이번주 리밸런싱 완료(2026-07-06)"), live=True)
        self.assertIn("매매하지 않음", m)
        self.assertIn("이미 이번주 리밸런싱 완료", m)

    def test_no_targets_model_ran(self):
        m = stock_message("KR 주식", _r(targets=[], buys=[]), live=True)
        self.assertIn("모델 실행됐으나 유효 종목 없음", m)
        self.assertIn("다음 평일 재시도", m)

    def test_already_aligned(self):
        m = stock_message("KR 주식", _r(buys=[]), live=True)
        self.assertIn("이미 목표 정렬", m)

    def test_orders_listed_with_counts(self):
        placed = [
            {"symbol": "005930", "qty": 3, "accepted": True, "filled": True, "filled_qty": 3},
            {"symbol": "000660", "qty": 1, "accepted": True, "filled": False, "filled_qty": 0},
            {"symbol": "035420", "qty": 0, "accepted": False, "msg": "수량<1"},
        ]
        m = stock_message("KR 주식", _r(buys=["005930", "000660", "035420"], placed=placed), live=True)
        for sym in ("005930", "000660", "035420"):
            self.assertIn(sym, m)
        self.assertIn("접수 2건 / 체결 1건", m)
        self.assertIn("체결 3주", m)
        self.assertIn("접수(미체결)", m)
        self.assertIn("거부/스킵", m)

    def test_sell_and_buy_lines_labeled(self):
        placed = [
            {"symbol": "031980", "side": "SELL", "qty": 5, "accepted": True, "filled": True, "filled_qty": 5},
            {"symbol": "005930", "side": "BUY", "qty": 3, "accepted": True, "filled": True, "filled_qty": 3},
        ]
        m = stock_message("KR 주식", _r(buys=["005930"], sells=["031980"], placed=placed), live=True)
        self.assertIn("매도 031980 5주 — 체결 5주", m)
        self.assertIn("매수 005930 3주 — 체결 3주", m)
        self.assertIn("접수 2건 / 체결 2건", m)

    def test_sells_only_listed_not_aligned(self):
        # 매수 없이 이탈 매도만 발주된 주 — '이미 목표 정렬'이 아니라 주문 목록 표시
        placed = [{"symbol": "031980", "side": "SELL", "qty": 5, "accepted": True,
                   "filled": False, "filled_qty": 0}]
        m = stock_message("KR 주식", _r(buys=[], sells=["031980"], placed=placed), live=True)
        self.assertNotIn("이미 목표 정렬", m)
        self.assertIn("매도 031980 5주 — 접수(미체결)", m)
        self.assertIn("체결 0건", m)

    def test_zero_fills_notes_retry(self):
        placed = [{"symbol": "005930", "qty": 3, "accepted": True, "filled": False, "filled_qty": 0}]
        m = stock_message("US 주식", _r(buys=["005930"], placed=placed), live=True)
        self.assertIn("체결 0건", m)
        self.assertIn("다음 평일 재시도", m)

    def test_dry_run_prefix(self):
        m = stock_message("KR 주식", _r(), live=False)
        self.assertIn("(DRY-RUN)", m)
        self.assertIn("계획만 산출", m)


class TestCoinMessage(unittest.TestCase):
    def test_no_trades(self):
        m = coin_message([{"action": "HOLD", "symbol": "KRW-BTC"}], 0, {"KRW-BTC": 0.0}, dry_run=False)
        self.assertIn("매매하지 않음", m)
        self.assertIn("결정 1건 기록", m)

    def test_trades_with_rejected(self):
        decisions = [
            {"action": "BUY", "symbol": "KRW-BTC", "quantity": "0.01", "price": 100_000_000, "executed": True},
            {"action": "SELL", "symbol": "KRW-ETH", "quantity": "0.5", "price": 5_000_000, "executed": False},
        ]
        m = coin_message(decisions, 1, {"KRW-BTC": 0.5}, dry_run=False,
                         balances={"a": {"cash": 1_000_000, "equity": 2_000_000}})
        self.assertIn("BUY KRW-BTC", m)
        self.assertIn("체결", m)
        self.assertIn("거부(잔고/보유 부족)", m)
        self.assertIn("매매 2건 / 체결 1 / 거부 1", m)
        self.assertIn("계좌 a", m)

    def test_dry_run_prefix(self):
        m = coin_message([], 0, {}, dry_run=True)
        self.assertIn("(DRY-RUN)", m)


class TestErrorMessage(unittest.TestCase):
    def test_type_message_and_location(self):
        try:
            raise ValueError("잔고 조회 실패")
        except ValueError as e:
            m = error_message("KR 주식", e)
        self.assertIn("[KR 주식]", m)
        self.assertIn("ValueError", m)
        self.assertIn("잔고 조회 실패", m)
        self.assertIn("test_notify_messages.py", m)   # 파일:라인 위치 표기


if __name__ == "__main__":
    unittest.main()
