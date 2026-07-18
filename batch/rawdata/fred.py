"""매크로 시계열 적재 (단일 책임: FRED API → macro_daily).

전 종목 공통 레짐 피처(금리·수익률곡선·VIX·환율·유가). 휴일은 전일캐리(ffill).
FRED_API_KEY 필요(.env). 재실행 멱등(ReplacingMergeTree) → 일별 증분은 재실행만 하면 됨.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.rawdata.fred [--start 2018-01-01]
"""
import argparse
import sys

import httpx
import pandas as pd

from common.clickhouse_client import create_client
from common.config import FRED_API_KEY

_SERIES = {"dgs10": "DGS10", "dgs2": "DGS2", "dgs3mo": "DGS3MO",
           "t10y2y": "T10Y2Y", "t10y3m": "T10Y3M", "vix": "VIXCLS",
           "usdkrw": "DEXKOUS", "dxy": "DTWEXBGS", "wti": "DCOILWTICO"}
_COLS = ["date", *_SERIES.keys()]
_API = "https://api.stlouisfed.org/fred/series/observations"


def _fetch(sid: str, key: str, client: httpx.Client, start: str) -> dict:
    r = client.get(_API, params={"series_id": sid, "api_key": key, "file_type": "json",
                                 "observation_start": start})
    r.raise_for_status()
    return {o["date"]: float(o["value"]) for o in r.json()["observations"] if o["value"] not in (".", "")}


def build_macro(start: str = "2018-01-01") -> pd.DataFrame:
    key = FRED_API_KEY
    if not key:
        raise RuntimeError("FRED_API_KEY 미설정(.env)")
    data = {}
    with httpx.Client(timeout=30) as c:
        for name, sid in _SERIES.items():
            data[name] = _fetch(sid, key, c, start)
    df = pd.DataFrame(data)
    df.index = pd.to_datetime(df.index)
    return df.sort_index().ffill().dropna()        # 휴일 전일캐리, 선두 결측 제거


def store(start: str = "2018-01-01", log=print) -> int:
    df = build_macro(start)
    rows = [[d.date(), *(float(v) for v in df.loc[d])] for d in df.index]
    if not rows:
        raise RuntimeError("[fred] 적재 0행 — FRED 응답/시리즈 확인(전체 실패의 조용한 성공 처리 방지)")
    create_client().insert("macro_daily", rows, column_names=_COLS)
    log(f"[fred] {len(rows):,}일 ({df.index[0].date()}~{df.index[-1].date()}) → macro_daily")
    return len(rows)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="FRED 매크로 → macro_daily")
    p.add_argument("--start", default="2018-01-01")
    a = p.parse_args(argv)
    store(a.start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
