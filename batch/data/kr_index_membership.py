"""KR 지수 PIT 멤버십 적재 (단일 책임: KRX → index_membership/index_changes의 KR 분).

KOSPI200(1028)·KOSDAQ150(2203)의 시점별 구성종목을 날짜 그리드(기본 월별)로 스냅샷,
연속 스냅샷 diff로 멤버십 구간·편입편출 이벤트를 만든다. source='KRX'.

⚠ pykrx 로그인·.env 선로드는 batch.data._krx_session이 일원화(거기서 stock·require_login import).
한계: 변경 시점 해상도 = 샘플링 주기(기본 월, US fja05680의 정확일과 달리 근사). --freq로 조절.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.kr_index_membership [--start 2018-01-01] [--freq BMS]
"""
import argparse
import sys
import time
from datetime import date

import pandas as pd

from batch.data._krx_session import require_login, stock
from common.clickhouse_client import create_client

_INDICES = {"KOSPI200": "1028", "KOSDAQ150": "2203"}
_FAR = date(2099, 12, 31)            # 현재 멤버 표식(US 적재와 동일 규약)


def _snapshots(code: str, grid: list, sleep: float) -> list:
    """[(date, frozenset(구성종목))] — 빈 응답일(휴장 등)은 제외, 날짜 오름차순."""
    out = []
    for d in grid:
        members = stock.get_index_portfolio_deposit_file(code, d.strftime("%Y%m%d"))
        time.sleep(sleep)
        if members:
            out.append((d.date(), frozenset(members)))
    return out


def _diff(name: str, snaps: list) -> tuple:
    """스냅샷 시계열 → (멤버십 구간 rows, 편입편출 이벤트 rows)."""
    intervals, changes = [], []
    prev, open_start, last_seen = frozenset(), {}, {}
    for d, cur in snaps:
        for sym in cur - prev:                       # 편입
            open_start[sym] = d
            if prev:                                 # 첫 스냅샷(prev 비어있음)은 add 아님(기준선)
                changes.append([d, sym, name, "add"])
        for sym in prev - cur:                       # 편출
            changes.append([d, sym, name, "drop"])
            if sym in open_start:
                intervals.append([sym, name, open_start.pop(sym), last_seen[sym]])
        for sym in cur:
            last_seen[sym] = d
        prev = cur
    for sym, start in open_start.items():            # 최신까지 멤버 → 현재 멤버
        intervals.append([sym, name, start, _FAR])
    return intervals, changes


def store_kr_membership(start="2018-01-01", freq="BMS", sleep=0.3, log=print):
    require_login()
    today = pd.Timestamp.today().normalize()
    grid = list(pd.date_range(start=start, end=today, freq=freq))
    if not grid or grid[-1] != today:
        grid.append(today)                           # 최신 구성 포착
    ch = create_client()
    n_mem = n_chg = 0
    for name, code in _INDICES.items():
        snaps = _snapshots(code, grid, sleep)
        if not snaps:
            log(f"[kr-membership] {name}: 스냅샷 없음(건너뜀)")
            continue
        intervals, changes = _diff(name, snaps)
        if intervals:
            ch.insert("index_membership", [r + ["KRX"] for r in intervals],
                      column_names=["symbol", "index_name", "start_date", "end_date", "source"])
        if changes:
            ch.insert("index_changes", [r + ["KRX"] for r in changes],
                      column_names=["date", "symbol", "index_name", "action", "source"])
        n_mem += len(intervals); n_chg += len(changes)
        log(f"[kr-membership] {name}: 스냅샷 {len(snaps)} → 멤버십 {len(intervals)}구간, 이벤트 {len(changes)}건")
    log(f"[kr-membership] 완료: 멤버십 {n_mem}구간, 이벤트 {n_chg}건")
    return n_mem, n_chg


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="KRX KOSPI200·KOSDAQ150 PIT 멤버십 → ClickHouse")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--freq", default="BMS", help="pandas 그리드 빈도(BMS=영업월초, W=주, B=영업일)")
    p.add_argument("--sleep", type=float, default=0.3, help="호출 간 대기(초)")
    a = p.parse_args(argv)
    store_kr_membership(start=a.start, freq=a.freq, sleep=a.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
