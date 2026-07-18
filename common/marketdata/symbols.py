"""거래 종목 목록 해석 (단일 책임: 정적 설정 또는 업비트 전체 동적).

SUBSCRIBE_ALL_KRW 면 업비트 전체 KRW 마켓을 동적 구독(캐시, 실패 시 정적 폴백),
아니면 정적 SYMBOLS 목록을 쓴다.
"""
import time

from common.config import SUBSCRIBE_ALL_KRW, SYMBOLS
from common.marketdata.upbit_markets import fetch_krw_markets

_MARKETS_TTL = 3600.0  # 동적 목록 캐시(마켓 변동은 드묾)
_FAIL_TTL = 60.0       # 조회 실패 후 재시도 억제(초) — 콜드 캐시 재시도 폭주/블로킹 방지
_cache: list[str] = []
_cache_at = -1e9
_fail_at = -1e9


def resolve_symbols() -> list[str]:
    """구독/조회 대상 종목 목록."""
    global _cache, _cache_at, _fail_at
    if not SUBSCRIBE_ALL_KRW:
        return SYMBOLS
    now = time.monotonic()
    stale = not _cache or now - _cache_at >= _MARKETS_TTL
    backing_off = now - _fail_at < _FAIL_TTL
    if stale and not backing_off:
        try:
            _cache = fetch_krw_markets()
            _cache_at = now
        except Exception as e:
            _fail_at = now
            print(f"[symbols] 업비트 마켓 조회 실패: {e}; 폴백={SYMBOLS}")
            return _cache or SYMBOLS
    return _cache or SYMBOLS


def _market_symbols(ch, market: str) -> list[str]:
    """저장된 stock_candles_1d의 시장별 종목 목록(distinct·정렬)."""
    return [r[0] for r in ch.query(
        "SELECT DISTINCT symbol FROM stock_candles_1d WHERE market={m:String} ORDER BY symbol",
        parameters={"m": market}).result_rows]


def get_us_symbols(ch) -> list[str]:
    """US 일봉 종목 목록. 펀더멘털·13F·섹터 수집 공통 쿼리."""
    return _market_symbols(ch, "US")


def get_kr_symbols(ch) -> list[str]:
    """KR 일봉 종목 목록. KRX 수급·공매도·외국인보유 수집 공통 쿼리."""
    return _market_symbols(ch, "KR")
