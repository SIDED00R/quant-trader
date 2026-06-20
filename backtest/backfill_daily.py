"""일봉 장기 백필 실행 (단일 책임: CLI → upbit_daily → ClickHouse candles_1d).

예) .venv/Scripts/python -m backtest.backfill_daily --symbols KRW-BTC,KRW-ETH --days 2000
업비트 일봉을 받아 ClickHouse candles_1d에 적재한다(저회전 추세 전략의 장기 백테스트용).
사전조건: docker compose up -d clickhouse + python -m scripts.init_db (candles_1d 생성).
"""
import argparse
import sys
from datetime import datetime, timezone

from common.clickhouse_client import create_client
from common.config import SYMBOLS
from backtest.upbit_daily import fetch_daily, upsert_clickhouse


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="업비트 일봉 장기 백필 → ClickHouse candles_1d")
    p.add_argument("--symbols", default=",".join(SYMBOLS), help="쉼표 구분")
    p.add_argument("--days", type=int, default=2000, help="과거 일수(기본 ~5.5년)")
    p.add_argument("--table", default="candles_1d", help="대상 ClickHouse 테이블")
    a = p.parse_args(argv)
    markets = [s.strip() for s in a.symbols.split(",") if s.strip()]
    if not markets:
        print("[daily] --symbols 가 비었습니다.", file=sys.stderr)
        return 2
    # 진행 중(미마감) 당일 일봉 제외 경계 = 오늘 00:00 UTC
    complete_until = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        client = create_client()
        total = 0
        for market in markets:
            rows = fetch_daily(market, a.days, complete_until)
            total += upsert_clickhouse(client, rows, a.table)
            print(f"[daily] {market}: {len(rows)}봉 적재")
    except Exception as e:   # 네트워크/CH 연결 실패 → fail-fast
        print(f"[daily] 실패: {e} (ClickHouse 기동·init_db 확인)", file=sys.stderr)
        return 2
    print(f"[daily] 완료: {markets} → {a.table} ({total}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
