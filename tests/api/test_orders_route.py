"""코인 주문 생성 라우트 검증 (api/routes/orders.py — 요청 검증 + place_order 위임, DB 무접촉).

api.main의 실제 app은 쓰지 않는다 — 미니앱(라우터만 include)으로 검증한다. 인증은
dependency_overrides로 우회(관심사 분리).
"""
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from api.routes import orders
from api.security import current_account_id


def _build_app():
    app = FastAPI()
    app.include_router(orders.router)
    app.dependency_overrides[current_account_id] = lambda: "acct1"
    return app


class TestCreateOrderValidation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_build_app())

    def test_invalid_side_400(self):
        res = self.client.post("/orders", json={
            "symbol": "KRW-BTC", "side": "HOLD", "type": "MARKET", "quantity": 1,
        })
        self.assertEqual(res.status_code, 400)

    def test_invalid_type_400(self):
        res = self.client.post("/orders", json={
            "symbol": "KRW-BTC", "side": "BUY", "type": "STOP", "quantity": 1,
        })
        self.assertEqual(res.status_code, 400)

    def test_non_positive_quantity_400(self):
        res = self.client.post("/orders", json={
            "symbol": "KRW-BTC", "side": "BUY", "type": "MARKET", "quantity": 0,
        })
        self.assertEqual(res.status_code, 400)

    def test_limit_without_price_400(self):
        res = self.client.post("/orders", json={
            "symbol": "KRW-BTC", "side": "BUY", "type": "LIMIT", "quantity": 1,
        })
        self.assertEqual(res.status_code, 400)


class TestCreateOrderSuccess(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_build_app())

    @patch("api.routes.orders.place_order", return_value="order-123")
    def test_valid_order_places_and_returns_pending(self, mock_place):
        res = self.client.post("/orders", json={
            "symbol": "KRW-BTC", "side": "BUY", "type": "LIMIT", "quantity": 0.5, "price": 100.0,
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"order_id": "order-123", "status": "PENDING"})
        mock_place.assert_called_once_with(
            account_id="acct1", symbol="KRW-BTC", side="BUY", type_="LIMIT",
            quantity=Decimal("0.5"), price=Decimal("100.0"),
        )

    @patch("api.routes.orders.place_order", return_value="order-124")
    def test_market_order_price_none(self, mock_place):
        self.client.post("/orders", json={
            "symbol": "KRW-BTC", "side": "SELL", "type": "MARKET", "quantity": 1,
        })
        mock_place.assert_called_once_with(
            account_id="acct1", symbol="KRW-BTC", side="SELL", type_="MARKET",
            quantity=Decimal("1.0"), price=None,
        )


if __name__ == "__main__":
    unittest.main()
