"""세션 인증 게이트 검증 (api/security.py — auth_gate 미들웨어·current_account_id).

api.main의 실제 app은 쓰지 않는다(lifespan이 DB/네트워크를 켠다) — 미니앱을 조립해 검증한다.
"""
import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from api import security


def _build_app() -> FastAPI:
    app = FastAPI()
    # main.py와 동일한 순서로 추가(세션이 먼저 채워진 뒤 게이트가 검사).
    app.add_middleware(BaseHTTPMiddleware, dispatch=security.auth_gate)
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/auth/_login_as")
    def _login_as(request: Request, email: str, account_id: str = "acct1"):
        """테스트 전용 세션 주입 라우트 — /auth/ 접두라 인증 없이 접근 가능."""
        request.session["user"] = {"email": email, "account_id": account_id}
        return {"ok": True}

    @app.get("/login")
    def login():
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/ping")
    def ping(account_id: str = Depends(security.current_account_id)):
        return {"account_id": account_id}

    return app


class TestAuthGate(unittest.TestCase):
    def setUp(self):
        self.app = _build_app()
        self.client = TestClient(self.app)

    @patch("api.security.AUTH_ENABLED", False)
    def test_auth_disabled_passes_with_demo_account(self):
        res = self.client.get("/api/ping")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["account_id"], "demo")

    @patch("api.security.ALLOWED_EMAILS", {"ok@x.com"})
    @patch("api.security.AUTH_ENABLED", True)
    def test_unauthenticated_api_request_401(self):
        res = self.client.get("/api/ping")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["detail"], "unauthorized")

    @patch("api.security.ALLOWED_EMAILS", {"ok@x.com"})
    @patch("api.security.AUTH_ENABLED", True)
    def test_unauthenticated_html_request_redirects_to_login(self):
        res = self.client.get(
            "/api/ping", headers={"accept": "text/html"}, follow_redirects=False
        )
        self.assertEqual(res.status_code, 307)   # RedirectResponse 기본 status_code
        self.assertEqual(res.headers["location"], "/login")

    @patch("api.security.ALLOWED_EMAILS", {"ok@x.com"})
    @patch("api.security.AUTH_ENABLED", True)
    def test_allowlist_violation_still_rejected(self):
        """세션에 유저가 있어도 allowlist 밖 이메일이면 401 — 신원확인 != 권한부여."""
        self.client.get("/auth/_login_as", params={"email": "outsider@x.com"})
        res = self.client.get("/api/ping")
        self.assertEqual(res.status_code, 401)

    @patch("api.security.ALLOWED_EMAILS", {"ok@x.com"})
    @patch("api.security.AUTH_ENABLED", True)
    def test_allowed_email_passes_with_session_account_id(self):
        self.client.get("/auth/_login_as", params={"email": "OK@X.com", "account_id": "acct-42"})
        res = self.client.get("/api/ping")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["account_id"], "acct-42")

    @patch("api.security.ALLOWED_EMAILS", {"ok@x.com"})
    @patch("api.security.AUTH_ENABLED", True)
    def test_public_paths_pass_without_login(self):
        for path, params in (("/healthz", None), ("/login", None),
                              ("/auth/_login_as", {"email": "anyone@x.com"})):
            res = self.client.get(path, params=params)
            self.assertEqual(res.status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
