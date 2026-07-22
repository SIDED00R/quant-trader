"""수동 주식 주문 라우트 검증 (api/routes/stock_orders.py — 접수/조회/취소, DB 무접촉).

api.main의 실제 app은 쓰지 않는다 — 미니앱(라우터만 include)으로 검증한다. 인증은 dependency_overrides로
우회(관심사 분리 — 인증 자체는 tests/api/test_security.py가 담당).
"""
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from starlette.testclient import TestClient

from api.routes import stock_orders
from api.security import current_account_id
from tests.helpers import fake_pool

_KST = ZoneInfo("Asia/Seoul")


def _build_app():
    app = FastAPI()
    app.include_router(stock_orders.router)
    app.dependency_overrides[current_account_id] = lambda: "acct1"
    return app


class TestCreateOrder(unittest.TestCase):
    def setUp(self):
        self.app = _build_app()
        self.client = TestClient(self.app)
        self.pool, self.conn = fake_pool()
        self.patcher = patch.object(stock_orders, "pool", self.pool)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.conn.execute.return_value.fetchone.return_value = (
            1, "PENDING", datetime(2026, 7, 22, 10, 0, 0, tzinfo=_KST),
        )

    def test_qty_and_amount_both_set_422(self):
        res = self.client.post("/stocks/orders", json={
            "market": "KR", "symbol": "005930", "side": "BUY", "qty": 1, "amount": 100.0,
        })
        self.assertEqual(res.status_code, 422)

    def test_qty_and_amount_both_missing_422(self):
        res = self.client.post("/stocks/orders", json={
            "market": "KR", "symbol": "005930", "side": "BUY",
        })
        self.assertEqual(res.status_code, 422)

    def test_naive_scheduled_at_gets_kst_tz(self):
        res = self.client.post("/stocks/orders", json={
            "market": "KR", "symbol": "005930", "side": "BUY", "qty": 1,
            "scheduled_at": "2026-07-22T10:00:00",
        })
        self.assertEqual(res.status_code, 200)
        params = self.conn.execute.call_args[0][1]
        at = params[6]
        self.assertEqual(at.tzinfo, _KST)
        self.assertEqual(at, datetime(2026, 7, 22, 10, 0, 0, tzinfo=_KST))

    def test_us_symbol_uppercased(self):
        self.client.post("/stocks/orders", json={
            "market": "US", "symbol": "aapl", "side": "BUY", "qty": 1,
        })
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[2], "AAPL")

    def test_kr_symbol_kept_as_is(self):
        self.client.post("/stocks/orders", json={
            "market": "KR", "symbol": "005930", "side": "BUY", "qty": 1,
        })
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[2], "005930")

    def test_account_id_scoped_in_insert(self):
        self.client.post("/stocks/orders", json={
            "market": "KR", "symbol": "005930", "side": "BUY", "qty": 1,
        })
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params[0], "acct1")


class TestCancelOrder(unittest.TestCase):
    def setUp(self):
        self.app = _build_app()
        self.client = TestClient(self.app)
        self.pool, self.conn = fake_pool()
        self.patcher = patch.object(stock_orders, "pool", self.pool)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_cancel_not_pending_409(self):
        self.conn.execute.return_value.fetchone.return_value = None
        res = self.client.post("/stocks/orders/5/cancel")
        self.assertEqual(res.status_code, 409)

    def test_cancel_pending_succeeds(self):
        self.conn.execute.return_value.fetchone.return_value = (5,)
        res = self.client.post("/stocks/orders/5/cancel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"id": 5, "status": "CANCELED"})
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params, (5, "acct1"))   # account_id 스코프 파라미터 포함


if __name__ == "__main__":
    unittest.main()
