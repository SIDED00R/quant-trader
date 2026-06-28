"""키움증권 OAuth2 접근토큰 발급·캐시 (단일 책임: 키움 인증).

접근토큰은 약 24h 유효하므로 만료 임박 시 자동 재발급한다(업비트엔 없던 인증 표면).
출처: docs/kiwoom.md §2 — POST {base}/oauth2/token, api-id au10001,
바디 grant_type=client_credentials + appkey + secretkey, 응답 필드는 token(=access_token 아님).
"""
from datetime import datetime, timedelta, timezone

import httpx

from common.config import KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_REST_BASE
from common.oauth_token import TokenCache

_TOKEN_PATH = "/oauth2/token"
_KST = timezone(timedelta(hours=9))


def _parse_expiry(expires_dt: str | None) -> datetime:
    """expires_dt('YYYYMMDDhhmmss', KST) → UTC. 파싱 실패 시 보수적으로 12h 후."""
    if expires_dt and len(expires_dt) == 14:
        try:
            return datetime.strptime(expires_dt, "%Y%m%d%H%M%S").replace(
                tzinfo=_KST
            ).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc) + timedelta(hours=12)


def _request_token() -> tuple[str, datetime]:
    if not (KIWOOM_APP_KEY and KIWOOM_APP_SECRET):
        raise RuntimeError("KIWOOM_APP_KEY/KIWOOM_APP_SECRET 미설정 — .env 확인")
    resp = httpx.post(
        f"{KIWOOM_REST_BASE}{_TOKEN_PATH}",
        headers={"Content-Type": "application/json;charset=UTF-8", "api-id": "au10001"},
        json={
            "grant_type": "client_credentials",
            "appkey": KIWOOM_APP_KEY,
            "secretkey": KIWOOM_APP_SECRET,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    body = resp.json()
    if str(body.get("return_code")) != "0":
        raise RuntimeError(
            f"키움 토큰 발급 실패: {body.get('return_code')} {body.get('return_msg')}"
        )
    return body["token"], _parse_expiry(body.get("expires_dt"))


_cache = TokenCache(_request_token, "kiwoom")


def get_access_token(force: bool = False) -> str:
    """유효한 접근토큰 반환(만료 임박 또는 force=True면 재발급)."""
    return _cache.get(force)
