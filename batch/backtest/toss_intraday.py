"""토스증권 분봉(1m) 수집 (단일 책임: REST /api/v1/candles interval=1m → ClickHouse stock_candles_1m).

토스는 interval로 1m·1d만 지원(실측). 분봉은 **정규장 외 시간(시간외) 봉도 토스가 주는 대로 원본 저장**한다
(정규장 필터·세션 처리는 백테스트/전략 층의 몫 — 수집기는 단순·충실하게).
응답 timestamp는 KST(+09:00) 표기 → UTC로 변환해 window_start(분 정밀도)로 저장. nextBefore(타임스탬프)로
역방향 페이지네이션. rate limit은 common/rate_limit(toss:MARKET_DATA_CHART, 5/s)로 페이싱하고,
429/5xx/전송오류 재시도·백오프는 common/http_client.get_json 에 위임한다.
stock_candles_1m는 ReplacingMergeTree(updated_at)라 (symbol, window_start) 재기록이 멱등(재실행 안전).
"""
from datetime import datetime, timedelta, timezone

import httpx

from common.config import TOSS_REST_BASE
from common.constants import COLUMNS_STOCK_CANDLES_1M, HTTP_PAGE
from common.http_client import get_json
from common.rate_limit import acquire
from common.toss_client import get_access_token

_URL = f"{TOSS_REST_BASE}/api/v1/candles"
_PAGE = HTTP_PAGE
_COLUMNS = COLUMNS_STOCK_CANDLES_1M


def _ts_utc(candle: dict) -> datetime:
    """캔들 timestamp(KST 표기) → tz-aware UTC datetime(분 정밀도)."""
    return datetime.fromisoformat(candle["timestamp"]).astimezone(timezone.utc)


def _row(symbol: str, candle: dict) -> list:
    """토스 분봉 1건 → ClickHouse row([symbol, window_start(UTC), o,h,l,c,v, currency, market])."""
    cur = candle["currency"]
    return [
        symbol, _ts_utc(candle),
        float(candle["openPrice"]), float(candle["highPrice"]), float(candle["lowPrice"]),
        float(candle["closePrice"]), float(candle["volume"]),
        cur, "KR" if cur == "KRW" else "US",
    ]


def fetch_minute(symbol: str, days: int, req_sleep: float = 0.0, log=print) -> list:
    """symbol의 최근 days일 분봉 rows를 시간 오름차순 반환(토스 원본, 정규장 외 포함).

    cutoff(=지금 UTC - days) 이전에 닿으면 종료. 미완료(현재 분) 봉은 제외(종가 미확정).
    rate limit(5/s)로 페이싱하므로 종목·기간이 크면 시간이 오래 걸린다(subset-first 권장).
    """
    headers = {"Authorization": f"Bearer {get_access_token()}"}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    now_minute = now.replace(second=0, microsecond=0)
    rows: list = []
    before = None
    with httpx.Client(timeout=20) as client:
        while True:
            acquire("toss", "MARKET_DATA_CHART")   # 5/s 페이싱(토큰버킷 블록)
            params = {"symbol": symbol, "interval": "1m", "count": _PAGE, "adjusted": True}
            if before is not None:
                params["before"] = before
            body = get_json(_URL, params, headers=headers, client=client, req_sleep=req_sleep)
            result = body.get("result", body)
            candles = result.get("candles", [])
            if not candles:
                break
            for c in candles:
                ts = _ts_utc(c)
                if ts >= now_minute:       # 미완료 현재 분 제외
                    continue
                if ts < cutoff:            # 요청 기간 밖
                    continue
                rows.append(_row(symbol, c))
            oldest = _ts_utc(candles[-1])
            log(f"[stock-1m] {symbol}: +{len(candles)} ~ {oldest.isoformat()}")
            next_before = result.get("nextBefore")
            if oldest < cutoff or next_before is None or len(candles) < _PAGE:
                break
            before = next_before
    rows.sort(key=lambda r: r[1])          # 시간 오름차순
    return rows


def upsert_clickhouse(client, rows: list, table: str = "stock_candles_1m") -> int:
    """rows를 ClickHouse table에 insert. ReplacingMergeTree라 재실행 멱등. 적재 행수 반환."""
    if not rows:
        return 0
    client.insert(table, rows, column_names=_COLUMNS)
    return len(rows)
