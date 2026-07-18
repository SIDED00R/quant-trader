"""일봉 장기 백필 실행 (단일 책임: CLI → upbit_daily → ClickHouse candles_1d).

예) .venv/Scripts/python -m batch.backtest.backfill_daily --symbols KRW-BTC,KRW-ETH --days 2000
업비트 일봉을 받아 ClickHouse candles_1d에 적재한다(저회전 추세 전략의 장기 백테스트용).
사전조건: docker compose up -d clickhouse + python -m scripts.init_db (candles_1d 생성).
"""
import argparse
import sys
from datetime import datetime, timezone

from common.clickhouse_client import create_client
from common.config import SYMBOLS
from common.marketdata.upbit_markets import fetch_krw_markets
from batch.backtest.upbit_daily import fetch_daily, upsert_clickhouse


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="업비트 일봉 장기 백필 → ClickHouse candles_1d")
    p.add_argument("--symbols", default=",".join(SYMBOLS), help="쉼표 구분")
    p.add_argument("--all-krw", action="store_true", help="업비트 전체 KRW 마켓(--symbols 무시)")
    p.add_argument("--days", type=int, default=2000, help="과거 일수(기본 ~5.5년)")
    p.add_argument("--table", default="candles_1d", help="대상 ClickHouse 테이블")
    a = p.parse_args(argv)
    if a.all_krw:
        try:
            markets = fetch_krw_markets()
        except Exception as e:
            print(f"[daily] 마켓 목록 조회 실패: {e}", file=sys.stderr)
            return 2
    else:
        markets = [s.strip() for s in a.symbols.split(",") if s.strip()]
    if not markets:
        print("[daily] 대상 종목이 없습니다.", file=sys.stderr)
        return 2
    # 진행 중(미마감) 당일 일봉 제외 경계 = 오늘 00:00 UTC
    complete_until = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:   # CH 연결 실패는 전체 치명 → fail-fast
        client = create_client()
    except Exception as e:
        print(f"[daily] ClickHouse 연결 실패: {e} (기동·init_db 확인)", file=sys.stderr)
        return 2
    total, failed = 0, []
    for market in markets:   # 종목별 격리 — 일시적 429/오류 1종목이 나머지(수백) 종목을 막지 않게
        try:
            latest = client.query(f"SELECT max(window_start) FROM {a.table} WHERE symbol={{m:String}}",
                                   parameters={"m": market}).result_rows[0][0]
            rows = fetch_daily(market, a.days, complete_until, since=latest)   # 증분: 저장된 과거 재수신 회피
            total += upsert_clickhouse(client, rows, a.table)
            print(f"[daily] {market}: {len(rows)}봉 적재")
        except Exception as e:
            failed.append(market)
            print(f"[daily] {market} 실패(건너뜀): {e}", file=sys.stderr)
    ok = len(markets) - len(failed)
    print(f"[daily] 완료: {ok}/{len(markets)}종목 → {a.table} ({total}행)")
    if failed:
        print(f"[daily] 실패 {len(failed)}종목: {failed} (재실행으로 보충 가능)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
