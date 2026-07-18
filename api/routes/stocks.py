"""주식 모의계좌 조회 (단일 책임: KIS 모의계좌 KR/US 잔고를 대시보드에 노출).

읽기 전용. KIS REST 직접 호출이라 콜드 응답이 느리다(토큰+잔고+매수가능 4~5콜) →
**60s TTL + stale-while-revalidate**: 캐시가 낡았으면 즉시 stale을 주고 백그라운드로 갱신해
탭 진입이 항상 즉답. KR/US 파이프라인은 독립이라 스레드 병렬(rate_limit 버킷이 페이싱).
"""
import asyncio
import concurrent.futures

from fastapi import APIRouter, HTTPException

from api.cache import cache_get, cache_get_stale, cache_set, refresh_in_background
from common.broker import kis_balance

router = APIRouter(prefix="/stocks")

CACHE_KEY = "stocks_account"
_TTL_SEC = 60


def compute_account() -> dict:
    """KR·US 잔고 실조회(느림 — 캐시/웜업 뒤에서만 호출)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        kr = ex.submit(kis_balance.kr_balance)
        us = ex.submit(kis_balance.us_balance)
        return {"kr": kr.result(), "us": us.result()}


@router.get("/account")
async def stock_account():
    """KR·US 모의계좌 잔고(현금·보유종목)."""
    fresh = cache_get(CACHE_KEY, _TTL_SEC)
    if fresh is not None:
        return fresh
    stale = cache_get_stale(CACHE_KEY)
    if stale is not None:                     # 즉시 응답 + 백그라운드 갱신(다음 조회는 fresh)
        asyncio.create_task(refresh_in_background(CACHE_KEY, compute_account))
        return stale
    try:                                      # 콜드(기동 직후 웜업 전 등) — 동기 조회
        out = await asyncio.to_thread(compute_account)
    except Exception as e:
        raise HTTPException(503, f"KIS 잔고 조회 실패: {e}")
    cache_set(CACHE_KEY, out)
    return out
