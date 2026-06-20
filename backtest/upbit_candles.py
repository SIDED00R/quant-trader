"""업비트 분봉 수집·캐시 (단일 책임: Upbit REST 캔들 → 로컬 CSV 캐시 + 로드).

과거 분봉은 REST(/v1/candles/minutes/{unit})를 'to' 역방향 페이지네이션(200/요청)으로 받는다.
종목별 CSV(ts_ms,open,high,low,close,volume,dt_utc)로 캐시한다. ts_ms는 **봉 시작(window_start)**의
epoch ms다(업비트 candle_date_time_utc 기준) — ClickHouse 소스의 window_start와 가상시계를 일치시키고
분 단위 멱등 중복제거를 보장한다(업비트 응답의 timestamp=마지막 체결시각은 쓰지 않는다).

재개·증분: 캐시가 있으면 ① 최신 방향(newest_cached~now)과 ② 과거 방향(oldest_cached~cutoff)을 모두 보충한다.
완료 시 시간 오름차순 정렬·중복제거(finalize)하며, tmp+os.replace로 원자적 교체해 중단 시 캐시를 보존한다.
공개 API라 인증 불필요. 429/5xx/네트워크 오류는 지수 백오프 재시도, 요청 간 sleep으로 호출량 절제. 진행 중(미마감) 현재 분봉은 기록하지 않는다.
로드는 종목별 정렬 캐시를 (ts, symbol) 전역 순서로 merge해 BTick(종가)을 yield한다.
"""
import csv
import heapq
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from backtest.models import BTick

_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
_HEADER = ["ts_ms", "open", "high", "low", "close", "volume", "dt_utc"]
_PAGE = 200
_MAX_RETRIES = 6
_MAX_BACKOFF = 30.0


def _backoff(attempt: int) -> float:
    """지수 백오프 초(상한 _MAX_BACKOFF) — 라이브 ingester(upbit_ws.py)와 동일 house 패턴."""
    return min(1.0 * (2 ** attempt), _MAX_BACKOFF)


def cache_path(cache_dir: str, market: str, unit: int) -> str:
    return os.path.join(cache_dir, f"{unit}m", f"{market}.csv")


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _ws_ms(candle: dict) -> int:
    """봉 시작(window_start) epoch ms — 정체성·정렬·BTick.ts의 정본 키."""
    return int(_parse_dt(candle["candle_date_time_utc"]).timestamp() * 1000)


def _get(client: httpx.Client, unit: int, params: dict, req_sleep: float) -> list:
    url = _URL.format(unit=unit)
    for attempt in range(_MAX_RETRIES):
        try:
            r = client.get(url, params=params)
        except httpx.TransportError:               # 타임아웃/연결오류 → 지수 백오프 재시도
            time.sleep(_backoff(attempt))
            continue
        if r.status_code == 429 or r.status_code >= 500:  # 레이트리밋/일시적 서버오류 → 지수 백오프 재시도
            time.sleep(_backoff(attempt))
            continue
        r.raise_for_status()
        time.sleep(req_sleep)
        return r.json()
    raise RuntimeError(f"upbit fetch failed after retries: {params}")


def _scan_dt(path: str):
    """캐시의 (oldest_dt, newest_dt). 없으면 (None, None)."""
    if not os.path.exists(path):
        return None, None
    oldest = newest = None
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dt = _parse_dt(row["dt_utc"])
            if oldest is None or dt < oldest:
                oldest = dt
            if newest is None or dt > newest:
                newest = dt
    return oldest, newest


def backfill(markets, unit, days, cache_dir, req_sleep=0.12, log=print):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    bucket_sec = unit * 60
    complete_until_ms = (int(now.timestamp()) // bucket_sec) * bucket_sec * 1000  # unit 경계 정렬 — unit>1 시 미마감 봉 올바르게 제외
    for market in markets:
        _backfill_one(market, unit, cutoff, complete_until_ms, cache_dir, req_sleep, log)
        _finalize(cache_path(cache_dir, market, unit), market, log)


def _fetch_backward(client, writer, fh, market, unit, to, lower_bound, complete_until_ms, req_sleep, log):
    """'to'부터 과거로 페이지네이션하며 append. oldest<=lower_bound 또는 페이지<200이면 종료.
    진행 중(window_start >= complete_until_ms)인 현재 분봉은 종가 미확정이라 기록하지 않는다."""
    fetched = 0
    while True:
        params = {"market": market, "count": _PAGE}
        if to is not None:
            params["to"] = to.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = _get(client, unit, params, req_sleep)
        if not rows:
            break
        for c in rows:
            ws = _ws_ms(c)
            if ws >= complete_until_ms:          # 미마감 현재 분봉은 건너뜀(종가 미확정)
                continue
            writer.writerow([ws, c["opening_price"], c["high_price"], c["low_price"],
                             c["trade_price"], c["candle_acc_trade_volume"], c["candle_date_time_utc"]])
        fh.flush()                              # 페이지마다 영속(중단 시 보존)
        fetched += len(rows)
        oldest_dt = _parse_dt(rows[-1]["candle_date_time_utc"])
        log(f"[backfill] {market}: +{len(rows)} (누적 {fetched}) ~ {oldest_dt.date()}")
        if (lower_bound is not None and oldest_dt <= lower_bound) or len(rows) < _PAGE:
            break
        to = oldest_dt
    return fetched


def _backfill_one(market, unit, cutoff, complete_until_ms, cache_dir, req_sleep, log):
    path = cache_path(cache_dir, market, unit)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    oldest, newest = _scan_dt(path)
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f, httpx.Client(timeout=20) as client:
        w = csv.writer(f)
        if new_file:
            w.writerow(_HEADER)
        if newest is not None:                  # 최신 방향 보충: (newest, 직전 완료 분]
            _fetch_backward(client, w, f, market, unit, None, newest, complete_until_ms, req_sleep, log)
        if oldest is None or oldest > cutoff:   # 과거 방향 보충/신규: [cutoff, oldest)
            _fetch_backward(client, w, f, market, unit, oldest, cutoff, complete_until_ms, req_sleep, log)


def _finalize(path: str, market: str, log):
    """시간 오름차순 정렬 + window_start 중복 제거. tmp+os.replace로 원자적 교체."""
    if not os.path.exists(path):
        return
    seen: dict[int, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            seen[int(row["ts_ms"])] = row
    rows = [seen[k] for k in sorted(seen)]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_HEADER)
        w.writeheader()
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)                       # 동일 FS 원자적 교체(중단되어도 원본 보존)
    log(f"[backfill] {market}: finalize {len(rows)} 봉 (정렬·중복제거)")


def _read_rows(market, unit, cache_dir, start_ms, end_ms):
    """정렬된 캐시를 (ts_ms, market, close) 오름차순 stream. 비정렬(미finalize)이면 에러."""
    path = cache_path(cache_dir, market, unit)
    if not os.path.exists(path):
        return
    prev = None
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = int(row["ts_ms"])
            if prev is not None and ts < prev:  # heapq.merge 전제(오름차순) 위반 방어
                raise ValueError(f"{market} 캐시가 시간 오름차순이 아닙니다(finalize 필요). 캐시 삭제 후 재백필하세요.")
            prev = ts
            if start_ms is not None and ts < start_ms:
                continue
            if end_ms is not None and ts >= end_ms:
                continue
            yield (ts, market, row["close"])


def load(markets, unit, cache_dir, start_ms=None, end_ms=None):
    """종목별 정렬 캐시를 (ts, symbol) 전역 순서로 merge해 BTick(종가) yield."""
    streams = [_read_rows(m, unit, cache_dir, start_ms, end_ms) for m in markets]
    for ts, market, close in heapq.merge(*streams, key=lambda x: (x[0], x[1])):
        yield BTick(market, Decimal(close), ts / 1000.0)
