"""토스증권 일봉 장기 수집 (단일 책임: REST /api/v1/candles → ClickHouse stock_candles_1d).

일봉은 1행/일이라 수년치도 적은 호출로 받는다(페이지 200/요청, nextBefore 역방향 페이지네이션).
응답 timestamp는 KR/US 모두 KST(+09:00) 표기 → 현지 날짜만 취해 00:00 UTC window_start로 정규화한다
(하루 1행). 수정주가(adjusted=true) 기준으로 받는다(백테스트엔 수정주가가 적합).
stock_candles_1d는 ReplacingMergeTree(updated_at)라 (symbol, window_start) 재기록이 멱등(재실행 안전).
재시도/백오프는 공용 common/http_client.get_json 에 위임한다.
"""
from datetime import datetime, timedelta, timezone

import httpx

from common.config import TOSS_REST_BASE
from common.constants import COLUMNS_STOCK_CANDLES_1D, HTTP_PAGE
from common.http_client import get_json
from common.toss_client import get_access_token

_URL = f"{TOSS_REST_BASE}/api/v1/candles"
_PAGE = HTTP_PAGE
_KST = timezone(timedelta(hours=9))
_COLUMNS = COLUMNS_STOCK_CANDLES_1D


def _get(client: httpx.Client, params: dict, headers: dict, req_sleep: float) -> dict:
    """캔들 1페이지 result를 반환 — 공용 재시도 GET에 위임."""
    body = get_json(_URL, params, headers=headers, client=client, req_sleep=req_sleep)
    return body.get("result", body)


def fetch_daily(symbol: str, days: int, req_sleep: float = 0.25, log=print) -> list:
    """symbol의 최근 days일 일봉 rows([symbol, window_start, o,h,l,c,v, currency, market])를 반환(시간 오름차순).

    미마감 당일(KST) 봉은 제외한다. cutoff(=오늘 KST - days)보다 과거에 닿으면 종료.
    토큰은 발급 시점 1회 사용 — 백필은 단일 프로세스·24h 미만이라 만료 전 완료된다.
    """
    headers = {"Authorization": f"Bearer {get_access_token()}"}
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
    rows.sort(key=lambda r: r[1])                           # 시간 오름차순(삽입 안정성·디버깅 편의)
    return rows


def upsert_clickhouse(client, rows: list, table: str = "stock_candles_1d") -> int:
    """rows를 ClickHouse table에 insert. ReplacingMergeTree라 재실행 멱등. 적재 행수 반환."""
    if not rows:
        return 0
    client.insert(table, rows, column_names=_COLUMNS)
    return len(rows)
