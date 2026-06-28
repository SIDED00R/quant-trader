"""HTTP GET + 재시도 (단일 책임: 429/5xx/전송오류 지수 백오프 GET).

Upbit·Toss·KIS REST 수집·조회의 공통 재시도 루프. 호출부는 응답 후처리(result 추출·
rt_cd 검증 등)만 담당한다. 영속 client를 주면 재사용(페이지네이션), 없으면 1회성 생성.
"""
import time

import httpx

from common.constants import HTTP_MAX_BACKOFF, HTTP_MAX_RETRIES, HTTP_TIMEOUT


def _backoff(attempt: int, max_backoff: float = HTTP_MAX_BACKOFF) -> float:
    """지수 백오프 초(상한 max_backoff)."""
    return min(1.0 * (2 ** attempt), max_backoff)


def get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    client: httpx.Client | None = None,
    max_retries: int = HTTP_MAX_RETRIES,
    max_backoff: float = HTTP_MAX_BACKOFF,
    req_sleep: float = 0.0,
    timeout: float = HTTP_TIMEOUT,
):
    """GET → JSON. 429/5xx/전송오류는 지수 백오프 재시도. 소진 시 RuntimeError."""
    own = client is None
    c = client or httpx.Client(timeout=timeout)
    kwargs: dict = {"params": params}
    if headers is not None:
        kwargs["headers"] = headers
    try:
        for attempt in range(max_retries):
            try:
                r = c.get(url, **kwargs)
            except httpx.TransportError:                       # 타임아웃/연결오류 → 백오프 재시도
                time.sleep(_backoff(attempt, max_backoff))
                continue
            if r.status_code == 429 or r.status_code >= 500:   # 레이트리밋/일시 서버오류 → 백오프 재시도
                time.sleep(_backoff(attempt, max_backoff))
                continue
            r.raise_for_status()
            if req_sleep:
                time.sleep(req_sleep)
            return r.json()
        raise RuntimeError(f"GET 재시도 소진({max_retries}회): {url} {params}")
    finally:
        if own:
            c.close()
