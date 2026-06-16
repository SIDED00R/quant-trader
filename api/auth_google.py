"""구글 OAuth 로그인 (단일 책임: OIDC 인증 흐름 + 계정 보장).

GOOGLE_CLIENT_ID/SECRET 설정 시 활성(common.config.AUTH_ENABLED).
허용 이메일(allowlist)만 통과시키고, 첫 로그인 시 사용자별 가상 계정
(account_id=google:<sub>)을 초기 자금과 함께 생성한다.
"""
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from common.config import (
    ALLOWED_EMAILS,
    AUTH_ENABLED,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    INITIAL_BALANCE,
    OAUTH_REDIRECT_URI,
)
from common.postgres_client import pool

router = APIRouter()

oauth = OAuth()
if AUTH_ENABLED:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def ensure_account(account_id: str) -> None:
    """계정이 없으면 초기 가상자금과 함께 생성(멱등)."""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO accounts (account_id, krw_balance) VALUES (%s, %s) "
            "ON CONFLICT (account_id) DO NOTHING",
            (account_id, INITIAL_BALANCE),
        )


@router.get("/login")
async def login(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse("/")
    return await oauth.google.authorize_redirect(request, OAUTH_REDIRECT_URI)


@router.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return HTMLResponse(
            f"<h2>로그인 실패</h2><p>{e.error}</p><a href='/login'>다시 시도</a>",
            status_code=400,
        )
    info = token.get("userinfo") or await oauth.google.userinfo(token=token)
    email = (info.get("email") or "").lower()
    if email not in ALLOWED_EMAILS:
        request.session.clear()
        return HTMLResponse(
            f"<h2>접근 거부</h2><p>허용되지 않은 계정입니다: {email}</p>"
            f"<a href='/logout'>다른 계정으로</a>",
            status_code=403,
        )
    account_id = f"google:{info['sub']}"
    ensure_account(account_id)
    request.session["user"] = {
        "email": email,
        "account_id": account_id,
        "name": info.get("name") or email,
    }
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@router.get("/me")
def me(request: Request):
    """현재 로그인 사용자 정보(대시보드 헤더용)."""
    if not AUTH_ENABLED:
        return {"auth_enabled": False, "email": None, "account_id": "demo"}
    user = request.session.get("user") or {}
    return {
        "auth_enabled": True,
        "email": user.get("email"),
        "account_id": user.get("account_id"),
        "name": user.get("name"),
    }
