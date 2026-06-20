"""업비트 일봉 장기 수집 (단일 책임: REST /candles/days → ClickHouse candles_1d).

일봉은 1행/일이라 수년치도 적은 호출로 받는다(분봉 대비 저비용 — 5년 ≈ 종목당 ~10요청).
'to' 역방향 페이지네이션(200/요청). window_start=일 시작(UTC). 진행 중(미마감) 당일 봉은 종가 미확정이라 제외.
candles_1d는 ReplacingMergeTree(updated_at)라 (symbol, window_start) 재기록이 멱등 병합된다(재실행 안전).
재시도/백오프는 분봉 수집(upbit_candles)과 동일 house 패턴을 재사용한다.
"""
import time
from datetime import datetime, timedelta, timezone

import httpx

from backtest.upbit_candles import _MAX_RETRIES, _backoff, _parse_dt

_URL = "https://api.upbit.com/v1/candles/days"
_PAGE = 200
_COLUMNS = ["symbol", "window_start", "open", "high", "low", "close", "volume"]


def _get(client: httpx.Client, params: dict, req_sleep: float) -> list:
    for attempt in range(_MAX_RETRIES):
        try:
            r = client.get(_URL, params=params)
        except httpx.TransportError:                       # 타임아웃/연결오류 → 지수 백오프
            time.sleep(_backoff(attempt))
            continue
        if r.status_code == 429 or r.status_code >= 500:   # 레이트리밋/일시적 서버오류 → 지수 백오프
            time.sleep(_backoff(attempt))
            continue
        r.raise_for_status()
        time.sleep(req_sleep)
        return r.json()
    raise RuntimeError(f"upbit daily fetch failed after retries: {params}")


def fetch_daily(market: str, days: int, complete_until: datetime, req_sleep: float = 0.12, log=print) -> list:
    """market의 최근 days일 일봉 rows([symbol, window_start, o,h,l,c,v])를 반환(시간 오름차순).

    complete_until 이상(미마감 당일)인 봉은 제외한다. cutoff(=now-days)보다 과거에 닿으면 종료.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list = []
    to = None
    with httpx.Client(timeout=20) as client:
        while True:
            params = {"market": market, "count": _PAGE}
            if to is not None:
                params["to"] = to.strftime("%Y-%m-%dT%H:%M:%SZ")
            page = _get(client, params, req_sleep)
            if not page:
                break
            for c in page:
                ws = _parse_dt(c["candle_date_time_utc"])
                if ws >= complete_until:                   # 미마감 당일 봉 제외(종가 미확정)
                    continue
                rows.append([market, ws, c["opening_price"], c["high_price"], c["low_price"],
                             c["trade_price"], c["candle_acc_trade_volume"]])
            oldest = _parse_dt(page[-1]["candle_date_time_utc"])
            log(f"[daily] {market}: +{len(page)} ~ {oldest.date()}")
            if oldest <= cutoff or len(page) < _PAGE:
                break
            to = oldest
    rows.sort(key=lambda r: r[1])                           # 시간 오름차순(삽입 안정성·디버깅 편의)
    return rows


def upsert_clickhouse(client, rows: list, table: str = "candles_1d") -> int:
    """rows를 ClickHouse table에 insert. ReplacingMergeTree라 재실행 멱등. 적재 행수 반환."""
    if not rows:
        return 0
    client.insert(table, rows, column_names=_COLUMNS)
    return len(rows)
