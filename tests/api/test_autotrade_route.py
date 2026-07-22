"""자동매매 토글 라우트 검증 (api/routes/autotrade.py — 계정별 auto_trade 조회/설정, DB 무접촉).

api.main의 실제 app은 쓰지 않는다 — 미니앱(라우터만 include)으로 검증한다. 인증은
dependency_overrides로 우회(관심사 분리).
"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from api.routes import autotrade
from api.security import current_account_id
from tests.helpers import fake_pool


def _build_app():
    app = FastAPI()
    app.include_router(autotrade.router)
    app.dependency_overrides[current_account_id] = lambda: "acct1"
    return app


class TestAutotradeRoute(unittest.TestCase):
    def setUp(self):
        self.app = _build_app()
        self.client = TestClient(self.app)
        self.pool, self.conn = fake_pool()
        self.patcher = patch.object(autotrade, "pool", self.pool)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_get_no_account_row_returns_disabled(self):
        self.conn.execute.return_value.fetchone.return_value = None
        res = self.client.get("/autotrade")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"enabled": False})

    def test_get_existing_account(self):
        self.conn.execute.return_value.fetchone.return_value = (True,)
        res = self.client.get("/autotrade")
        self.assertEqual(res.json(), {"enabled": True})
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params, ("acct1",))

    def test_post_updates_with_enabled_and_account_id(self):
        res = self.client.post("/autotrade", json={"enabled": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"enabled": True})
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params, (True, "acct1"))

    def test_post_disable(self):
        self.client.post("/autotrade", json={"enabled": False})
        params = self.conn.execute.call_args[0][1]
        self.assertEqual(params, (False, "acct1"))


if __name__ == "__main__":
    unittest.main()
