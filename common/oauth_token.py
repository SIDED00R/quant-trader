"""OAuth 접근토큰 캐시 (단일 책임: 스레드·프로세스 안전 토큰 캐시·선제 재발급).

브로커마다 발급 요청 본문·응답 파싱은 다르지만(키움/토스/KIS), "토큰을 캐시하고 만료 임박 시
재발급한다"는 메커니즘은 동일하다 — 그 공통부를 여기 한 곳에 둔다. 각 클라이언트는
`request_fn`(→ (token, expires_at(UTC)))만 주입한다.

토큰을 **파일에도 영속**(시스템 임시폴더, 레포 밖)한다 — KIS는 발급 횟수 제한이 빡빡해
매 프로세스(트레이더 매 실행 등)가 재발급하면 차단되므로, 프로세스 간에도 유효토큰을 재사용한다.
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

DEFAULT_EXPIRY_MARGIN = timedelta(minutes=10)  # 만료 10분 전 선제 재발급(브로커 공통)
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "broker_token_cache")


class TokenCache:
    """request_fn으로 발급한 토큰을 메모리+파일에 캐시. 만료 margin 전 또는 force 시 재발급."""

    def __init__(self, request_fn: Callable[[], tuple[str, datetime]], name: str,
                 margin: timedelta = DEFAULT_EXPIRY_MARGIN):
        self._request_fn = request_fn
        self._margin = margin
        self._name = name
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._file = os.path.join(_CACHE_DIR, f"{name}.json")
        self._load()

    def _load(self) -> None:
        """파일 캐시에서 유효 토큰 복원(없거나 손상되면 무시)."""
        try:
            with open(self._file, encoding="utf-8") as f:
                d = json.load(f)
            self._token = d["token"]
            self._expires_at = datetime.fromisoformat(d["expires_at"])
        except Exception:
            pass

    def _save(self) -> None:
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump({"token": self._token, "expires_at": self._expires_at.isoformat()}, f)
        except Exception as e:
            # 저장 실패는 비치명(메모리 캐시로 동작)이지만 조용히 넘기면 매 프로세스가 재발급
            # → KIS 발급한도 소진으로 이어질 수 있어 크게 남긴다.
            print(f"[{self._name}] 토큰 캐시 저장 실패({type(e).__name__}: {e}) — "
                  f"프로세스 간 토큰 재사용 불가, KIS 발급한도 소모 주의", file=sys.stderr)

    def get(self, force: bool = False) -> str:
        """유효한 토큰 반환(만료 임박 또는 force=True면 재발급, 발급 시 파일 갱신)."""
        with self._lock:
            now = datetime.now(timezone.utc)
            if force or self._token is None or now >= self._expires_at - self._margin:
                self._token, self._expires_at = self._request_fn()
                self._save()
                print(f"[{self._name}] 접근토큰 발급 (만료 {self._expires_at.isoformat()})")
            return self._token
