"""OAuth 접근토큰 캐시 (단일 책임: 스레드 안전 토큰 캐시·선제 재발급).

브로커마다 발급 요청 본문·응답 파싱은 다르지만(키움/토스/KIS), "토큰을 메모리에 캐시하고
만료 임박 시 재발급한다"는 메커니즘은 동일하다 — 그 공통부를 여기 한 곳에 둔다.
각 클라이언트는 `request_fn`(→ (token, expires_at(UTC)))만 주입한다.
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

DEFAULT_EXPIRY_MARGIN = timedelta(minutes=10)  # 만료 10분 전 선제 재발급(브로커 공통)


class TokenCache:
    """request_fn으로 발급한 토큰을 캐시. 만료 margin 전 또는 force 시 재발급."""

    def __init__(self, request_fn: Callable[[], tuple[str, datetime]], name: str,
                 margin: timedelta = DEFAULT_EXPIRY_MARGIN):
        self._request_fn = request_fn
        self._margin = margin
        self._name = name
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)

    def get(self, force: bool = False) -> str:
        """유효한 토큰 반환(만료 임박 또는 force=True면 재발급)."""
        with self._lock:
            now = datetime.now(timezone.utc)
            if force or self._token is None or now >= self._expires_at - self._margin:
                self._token, self._expires_at = self._request_fn()
                print(f"[{self._name}] 접근토큰 발급 (만료 {self._expires_at.isoformat()})")
            return self._token
