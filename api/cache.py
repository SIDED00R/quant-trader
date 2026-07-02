"""인프로세스 TTL 캐시 (단일 책임: API 응답 캐시 — 저장·조회·백그라운드 갱신).

uvicorn 단일 워커·단일 api 컨테이너 전제(현 compose)라 프로세스 dict로 충분하다.
배포 재시작은 lifespan 웜업(api.warmup)이 수 초 내 재충전한다.
⚠ workers>1 로 늘리면 Postgres 테이블 캐시(coding_recommend의 api_cache 방식)로 교체할 것.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)

_store: dict[str, tuple[datetime, Any]] = {}   # key → (저장시각 UTC, payload)
_inflight: set[str] = set()                    # 백그라운드 갱신 dogpile 가드


def cache_get(key: str, max_age_sec: float):
    """신선한 캐시 페이로드. 부재·만료 시 None."""
    hit = _store.get(key)
    if not hit:
        return None
    at, payload = hit
    if (datetime.now(timezone.utc) - at).total_seconds() > max_age_sec:
        return None
    return payload


def cache_get_stale(key: str):
    """수명 무관 캐시 페이로드 — 업스트림(KIS/ClickHouse) 장애 폴백·증분 캐시용."""
    hit = _store.get(key)
    return hit[1] if hit else None


def cache_set(key: str, payload) -> None:
    _store[key] = (datetime.now(timezone.utc), payload)


async def refresh_in_background(key: str, compute: Callable) -> None:
    """compute(동기 함수)를 스레드로 실행해 캐시 갱신. 동일 키 중복 갱신 방지, 실패는 로그만."""
    if key in _inflight:
        return
    _inflight.add(key)
    try:
        cache_set(key, await asyncio.to_thread(compute))
    except Exception as e:
        log.warning("캐시 갱신 실패 %s: %s", key, e)
    finally:
        _inflight.discard(key)
