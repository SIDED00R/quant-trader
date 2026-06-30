"""토스증권 OAuth2 접근토큰 발급·캐시 (단일 책임: 토스 인증).

OAuth2 client_credentials grant. POST {base}/oauth2/token 에 application/x-www-form-urlencoded
바디(grant_type/client_id/client_secret)를 보내고 OAuth2 표준 응답(access_token/expires_in,
키움식 return_code 아님)을 받는다. 이후 모든 호출은 Authorization: Bearer.
출처: 토스 Open API 스펙 §Auth.

⚠️ 클라이언트당 유효 토큰은 1개 — 재발급 시 이전 토큰이 즉시 무효화된다. 토큰은 공용
TokenCache를 통해 다른 브로커(키움/KIS)와 동일하게 파일에도 영속(시스템 임시폴더, 레포 밖)되어
프로세스 간 재사용된다. 단 '유효 토큰 1개' 제약상 같은 client_id를 여러 프로세스가 동시에/연달아
쓰면 재발급이 디스크에 남은 것을 포함해 서로의 토큰을 무효화할 수 있으므로, 향후 라이브 워커
추가 시 토큰 중앙화(또는 토스 전용 메모리 캐시)가 필요하다.
"""
from datetime import datetime, timedelta, timezone

import httpx

from common.config import TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_REST_BASE
from common.oauth_token import TokenCache

_TOKEN_PATH = "/oauth2/token"


def _request_token() -> tuple[str, datetime]:
    if not (TOSS_CLIENT_ID and TOSS_CLIENT_SECRET):
        raise RuntimeError("TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 미설정 — .env 확인")
    resp = httpx.post(
        f"{TOSS_REST_BASE}{_TOKEN_PATH}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": TOSS_CLIENT_ID,
            "client_secret": TOSS_CLIENT_SECRET,
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        raise RuntimeError(
            f"토스 토큰 발급 실패: {resp.status_code} "
            f"{body.get('error')} {body.get('error_description')}"
        )
    body = resp.json()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))
    return body["access_token"], expires_at


_cache = TokenCache(_request_token, "toss")


def get_access_token(force: bool = False) -> str:
    """유효한 접근토큰 반환(만료 임박 또는 force=True면 재발급)."""
    return _cache.get(force)
