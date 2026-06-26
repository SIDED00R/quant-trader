"""한국투자증권 KIS OAuth2 접근토큰 발급·캐시 (단일 책임: KIS 인증).

OAuth2 client_credentials grant. POST {base}/oauth2/tokenP 에 JSON 바디
(grant_type/appkey/appsecret)를 보내고 access_token(약 24h)을 받는다. 이후 주문/계좌 API는
Authorization: Bearer {token} + appkey/appsecret 헤더를 함께 쓴다(어댑터에서 구성).
출처: KIS Developers — 접근토큰 발급(/oauth2/tokenP).

⚠️ KIS는 접근토큰 발급 횟수에 제한이 있어 매 호출마다 재발급하면 차단된다. 토큰은
프로세스 메모리에 캐시하고 만료 임박 시에만 재발급한다(키움/토스 클라이언트와 동일 패턴).
"""
import threading
from datetime import datetime, timedelta, timezone

import httpx

from common.config import KIS_APPKEY, KIS_APPSECRET, KIS_REST_BASE

_TOKEN_PATH = "/oauth2/tokenP"
_EXPIRY_MARGIN = timedelta(minutes=10)  # 만료 10분 전 선제 재발급

_lock = threading.Lock()
_token: str | None = None
_expires_at = datetime.min.replace(tzinfo=timezone.utc)


def _request_token() -> tuple[str, datetime]:
    if not (KIS_APPKEY and KIS_APPSECRET):
        raise RuntimeError("KIS_APPKEY/KIS_APPSECRET 미설정 — .env 확인")
    resp = httpx.post(
        f"{KIS_REST_BASE}{_TOKEN_PATH}",
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": KIS_APPKEY,
            "appsecret": KIS_APPSECRET,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(
            f"KIS 토큰 발급 실패: {body.get('error_code')} {body.get('error_description') or body.get('msg1')}"
        )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 86400)))
    return body["access_token"], expires_at


def get_access_token(force: bool = False) -> str:
    """유효한 접근토큰 반환(만료 임박 또는 force=True면 재발급)."""
    global _token, _expires_at
    with _lock:
        now = datetime.now(timezone.utc)
        if force or _token is None or now >= _expires_at - _EXPIRY_MARGIN:
            _token, _expires_at = _request_token()
            print(f"[kis] 접근토큰 발급 (만료 {_expires_at.isoformat()})")
        return _token
