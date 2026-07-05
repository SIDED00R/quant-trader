"""US 지수 PIT 멤버십·편입편출 적재 (단일 책임: GitHub → index_membership/index_changes).

생존편향 부분 해결(시점별 소속) + add/drop 이벤트 신호. S&P500은 PIT 정확(fja05680/sp500),
NASDAQ-100 PIT는 무료 정형소스 부재 → 현재구성만(근사, 한계 명시). 재실행 멱등.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.us_membership
"""
import csv
import io
import sys
from datetime import date

import httpx
from dotenv import load_dotenv

load_dotenv()
from common.clickhouse_client import create_client

_UA = {"User-Agent": "coin-auto-trader research"}
_BASE = "https://raw.githubusercontent.com/fja05680/sp500/master"
_FAR = date(2099, 12, 31)


def _get(url: str, client: httpx.Client) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.text


def store_sp500(client: httpx.Client, ch, log=print) -> tuple:
    # 멤버십 기간(종목별 편입~편출)
    rows = list(csv.DictReader(io.StringIO(_get(f"{_BASE}/sp500_ticker_start_end.csv", client))))
    mem = [[r["ticker"].strip(), "SP500",
            date.fromisoformat(r["start_date"]),
            date.fromisoformat(r["end_date"]) if r.get("end_date") else _FAR]
           for r in rows if r.get("start_date")]
    if not mem:
        raise RuntimeError("[membership] 적재 0구간 — fja05680/sp500 CSV 포맷 확인(전체 실패의 조용한 성공 처리 방지)")
    ch.insert("index_membership", mem, column_names=["symbol", "index_name", "start_date", "end_date"])
    # 편입편출 이벤트
    ev = []
    for r in csv.DictReader(io.StringIO(_get(f"{_BASE}/sp500_changes_since_2019.csv", client))):
        d = date.fromisoformat(r["date"])
        for t in (r.get("add") or "").split(","):
            if t.strip():
                ev.append([d, t.strip(), "SP500", "add"])
        for t in (r.get("remove") or "").split(","):
            if t.strip():
                ev.append([d, t.strip(), "SP500", "drop"])
    ch.insert("index_changes", ev, column_names=["date", "symbol", "index_name", "action"])
    log(f"[membership] SP500: 멤버십 {len(mem)}구간, 이벤트 {len(ev)}건")
    return len(mem), len(ev)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ch = create_client()
    with httpx.Client(timeout=30, headers=_UA, follow_redirects=True) as client:
        store_sp500(client, ch)
    # NASDAQ-100 PIT: 무료 정형소스 부재 → 현재구성(batch/backtest/universe/nasdaq100.txt) 근사는 보류, 한계 명시
    print("[membership] 주의: NASDAQ-100 PIT은 무료 소스 부재로 미적재(현재구성만, 한계). S&P500은 PIT 정확.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
