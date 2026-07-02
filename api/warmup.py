"""기동 웜업 (단일 책임: 느린 캐시 선계산 — lifespan 백그라운드 태스크).

배포/재시작 직후 첫 방문자가 콜드 캐시 비용(앙상블 전체 히스토리·KIS 4~5콜, 수 초)을 물지 않게
기동 직후 백그라운드로 채운다. 개별 실패는 로그만 남기고 기동을 막지 않는다(coding_recommend 패턴).
"""
import asyncio
import logging

from api.cache import cache_set
from api.routes import stocks, strategy

log = logging.getLogger(__name__)

_TARGETS = (
    (strategy.CACHE_KEY, strategy.compute_ensemble),
    (stocks.CACHE_KEY, stocks.compute_account),
)


async def warm_caches() -> None:
    for key, compute in _TARGETS:
        try:
            cache_set(key, await asyncio.to_thread(compute))
        except Exception as e:
            log.warning("웜업 실패 %s: %s", key, e)
        await asyncio.sleep(0.5)
    log.info("캐시 웜업 완료")
