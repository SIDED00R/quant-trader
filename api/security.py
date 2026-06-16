"""세션 인증 게이트 (단일 책임: 로그인 보호 + 현재 계정 식별).

구글 OAuth(common.config.AUTH_ENABLED) 활성 시: 로그인 + 허용 이메일 세션만 통과.
미설정 시: 인증 비활성(로컬 개발) — 모든 요청 통과, 계정은 demo.
"""
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse

from common.config import ALLOWED_EMAILS, AUTH_ENABLED


def _is_public(path: str) -> bool:
    """인증 없이 접근 가능한 경로(로그인 흐름 + favicon)."""
    return (
        path in ("/login", "/logout", "/favicon.ico")
        or path.startswith("/auth/")
    )


def _session_user(request: Request) -> dict | None:
    user = request.session.get("user")
    if user and user.get("email", "").lower() in ALLOWED_EMAILS:
        return user
    return None


async def auth_gate(request: Request, call_next):
    """로그인하지 않은 요청을 차단(HTML은 /login 리다이렉트, API는 401)."""
    if not AUTH_ENABLED or _is_public(request.url.path):
        return await call_next(request)
    if _session_user(request) is not None:
        return await call_next(request)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/login")
    return JSONResponse({"detail": "unauthorized"}, status_code=401)


def current_account_id(request: Request) -> str:
    """로그인 사용자의 account_id (인증 비활성 시 demo)."""
    if not AUTH_ENABLED:
        return "demo"
    user = _session_user(request)
    if user is None:
        raise HTTPException(401, "unauthorized")
    return user["account_id"]
