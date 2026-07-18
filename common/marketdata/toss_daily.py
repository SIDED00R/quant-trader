"""토스증권 일봉 fetch (단일 책임: REST /api/v1/candles → 정규화 rows). app 이미지 안전(batch 비의존).

batch/candles/toss_daily_load.py에서 이동 — 텔레그램 차트 봇(app 이미지·수집 VM)이 주식 로컬 CH 없이
온디맨드로 일봉을 받기 위함. CH 적재(upsert_clickhouse)는 배치 책임이라 batch 측에 잔류하고 fetch만 이곳에 둔다.
응답 timestamp는 KR/US 모두 KST(+09:00) → 현지 날짜만 취해 00:00 UTC window_start로 정규화(하루 1행). 수정주가.
401(클라이언트당 유효 토큰 1개 제약 — 다른 워커의 재발급으로 무효화 가능)은 강제 재발급 후 1회 재시도한다.
재시도/백오프는 공용 common/http_client.get_json 에 위임.
"""
from datetime import datetime, timedelta, timezone

import httpx

from common.config import TOSS_REST_BASE
from common.constants import HTTP_PAGE
from common.http_client import get_json
from common.broker.toss_client import get_access_token

_URL = f"{TOSS_REST_BASE}/api/v1/candles"
_PAGE = HTTP_PAGE
_KST = timezone(timedelta(hours=9))


def _get(client: httpx.Client, params: dict, headers: dict, req_sleep: float) -> dict:
    """캔들 1페이지 result 반환 — 공용 재시도 GET에 위임."""
    body = get_json(_URL, params, headers=headers, client=client, req_sleep=req_sleep)
    return body.get("result", body)


def _fetch_pages(symbol: str, days: int, headers: dict, req_sleep: float, log) -> list:
    today_kst = datetime.now(_KST).date()
    cutoff = today_kst - timedelta(days=days)
    rows: list = []
    before = None
    with httpx.Client(timeout=20) as client:
        while True:
            params = {"symbol": symbol, "interval": "1d", "count": _PAGE, "adjusted": True}
            if before is not None:
                params["before"] = before
            result = _get(client, params, headers, req_sleep)
            candles = result.get("candles", [])
            if not candles:
                break
            for c in candles:
                d = datetime.fromisoformat(c["timestamp"]).date()
                if d >= today_kst:                         # 미마감 당일/미래 봉 제외(종가 미확정)
                    continue
                if d <= cutoff:                            # 요청 기간 밖
                    continue
                cur = c["currency"]
                rows.append([
                    symbol, datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                    float(c["openPrice"]), float(c["highPrice"]), float(c["lowPrice"]),
                    float(c["closePrice"]), float(c["volume"]),
                    cur, "KR" if cur == "KRW" else "US",
                ])
            oldest = datetime.fromisoformat(candles[-1]["timestamp"]).date()
            log(f"[stock-daily] {symbol}: +{len(candles)} ~ {oldest}")
            next_before = result.get("nextBefore")
            if oldest <= cutoff or next_before is None or len(candles) < _PAGE:
                break
            before = next_before
    rows.sort(key=lambda r: r[1])                          # 시간 오름차순
    return rows


def fetch_daily(symbol: str, days: int, req_sleep: float = 0.25, log=print) -> list:
    """symbol의 최근 days일 일봉 rows([symbol, window_start, o,h,l,c,v, currency, market]) 오름차순.

    미마감 당일(KST) 봉 제외. 401(토큰 무효)이면 강제 재발급 후 1회 재시도(크로스 워커 무효화 자가 회복).
    """
    # Accept-Encoding=identity: httpx 0.27.2 zstd 멀티청크 디코드 버그 우회.
    for attempt in (0, 1):
        headers = {"Authorization": f"Bearer {get_access_token(force=attempt == 1)}",
                   "Accept-Encoding": "identity"}
        try:
            return _fetch_pages(symbol, days, headers, req_sleep, log)
        except httpx.HTTPStatusError as e:
            if attempt == 0 and e.response is not None and e.response.status_code == 401:
                log(f"[stock-daily] {symbol}: 401 — 토큰 강제 재발급 후 1회 재시도")
                continue
            raise
    raise RuntimeError(f"{symbol}: fetch 실패(도달 불가)")   # for 루프는 항상 return/raise — 타입체커용 방어
