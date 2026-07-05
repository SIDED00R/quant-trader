"""US 섹터(SIC) 수집 (단일 책임: SEC submissions → stock_meta).

산업모멘텀(indmom, GKX top-7)·sector-neutral 피처용. SIC 4자리 + 2자리 major group(sector2).
캐시(sic_map.json). 재실행 멱등.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.sec_sector
"""
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
from batch.features import edgar
from common.cache import dump_json, load_json, refcache_path
from common.clickhouse_client import create_client
from common.symbols import get_us_symbols

_CACHE = refcache_path("sic_map.json")   # 참조캐시(영속 볼륨) — SIC는 사실상 불변(#218)


def fetch_sic(symbols, client, log=print) -> dict:
    cache = load_json(_CACHE, {})
    cikmap = edgar.ticker_cik_map()
    for i, t in enumerate(symbols):
        if t in cache:
            continue
        k = cikmap.get(t.upper())
        if not k:
            cache[t] = None; continue
        try:
            time.sleep(0.12)
            j = client.get(f"https://data.sec.gov/submissions/CIK{k}.json").json()
            cache[t] = {"sic": str(j.get("sic") or ""), "desc": j.get("sicDescription") or ""}
        except Exception:
            cache[t] = None
        if (i + 1) % 100 == 0:
            log(f"[sector] {i+1}/{len(symbols)}")
    dump_json(_CACHE, cache)
    return cache


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ch = create_client()
    us = get_us_symbols(ch)
    with httpx.Client(timeout=20, headers=edgar._UA) as c:
        sic = fetch_sic(us, c)
    rows = [[t, v["sic"], v["desc"], v["sic"][:2]] for t, v in sic.items() if v and v.get("sic")]
    ch.insert("stock_meta", rows, column_names=["symbol", "sic", "sic_desc", "sector2"])
    print(f"[sector] {len(rows)}종목 SIC 적재. 고유 sector2={len(set(r[3] for r in rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
