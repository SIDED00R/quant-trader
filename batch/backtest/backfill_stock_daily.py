"""주식 일봉 장기 백필 실행 (단일 책임: CLI → toss_daily → ClickHouse stock_candles_1d).

예) .venv/Scripts/python -m batch.backtest.backfill_stock_daily --symbols 005930,000660,AAPL --days 2000
토스증권 일봉(KR/US)을 받아 ClickHouse stock_candles_1d에 적재한다(주식 백테스트 입력).
사전조건: docker compose up -d clickhouse + python -m scripts.init_db (stock_candles_1d 생성),
.env에 TOSS_CLIENT_ID/SECRET 설정.
"""
import argparse
import sys

from batch.backtest.toss_daily import fetch_daily, upsert_clickhouse
from common.clickhouse_client import create_client

_DEFAULT_SYMBOLS = "005930,000660,AAPL"   # 잠정(8단계 유니버스 확정 전). KR=6자리 숫자, US=티커.


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="토스증권 주식 일봉 백필 → ClickHouse stock_candles_1d")
    p.add_argument("--symbols", default=_DEFAULT_SYMBOLS, help="쉼표 구분 (KR 6자리 / US 티커)")
    p.add_argument("--days", type=int, default=2000, help="과거 일수(기본 ~5.5년)")
    p.add_argument("--table", default="stock_candles_1d", help="대상 ClickHouse 테이블")
    a = p.parse_args(argv)
    symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
    if not symbols:
        print("[stock-daily] 대상 종목이 없습니다.", file=sys.stderr)
        return 2
    try:   # CH 연결 실패는 전체 치명 → fail-fast
        client = create_client()
    except Exception as e:
        print(f"[stock-daily] ClickHouse 연결 실패: {e} (기동·init_db 확인)", file=sys.stderr)
        return 2
    total, failed = 0, []
    for symbol in symbols:   # 종목별 격리 — 일시적 오류 1종목이 나머지를 막지 않게
        try:
            rows = fetch_daily(symbol, a.days)
            total += upsert_clickhouse(client, rows, a.table)
            print(f"[stock-daily] {symbol}: {len(rows)}봉 적재")
        except Exception as e:
            failed.append(symbol)
            print(f"[stock-daily] {symbol} 실패(건너뜀): {e}", file=sys.stderr)
    ok = len(symbols) - len(failed)
    print(f"[stock-daily] 완료: {ok}/{len(symbols)}종목 → {a.table} ({total}행)")
    if failed:
        print(f"[stock-daily] 실패 {len(failed)}종목: {failed} (재실행으로 보충 가능)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
