"""분봉 백필 실행 (단일 책임: CLI → upbit_candles.backfill).

예) .venv/Scripts/python -m backtest.backfill --unit 1 --days 730 --symbols KRW-BTC,KRW-ETH
업비트 REST에서 과거 분봉을 받아 로컬 캐시(기본 data/candles)에 저장한다. 이후 백테스트는 캐시를 읽는다.
"""
import argparse
import sys

from common.config import SYMBOLS
from backtest.upbit_candles import backfill


def main(argv=None) -> int:
    try:  # 진행 로그가 cp949 콘솔에서 크래시하지 않도록 UTF-8 강제
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="업비트 분봉 백필 → 로컬 캐시")
    p.add_argument("--symbols", default=",".join(SYMBOLS), help="쉼표 구분")
    p.add_argument("--unit", type=int, default=1, help="분봉 단위(1,3,5,15,30,60,240)")
    p.add_argument("--days", type=int, default=730, help="과거 일수(기본 2년)")
    p.add_argument("--cache-dir", default="data/candles", help="캐시 디렉터리")
    a = p.parse_args(argv)
    markets = [s.strip() for s in a.symbols.split(",") if s.strip()]
    backfill(markets, a.unit, a.days, a.cache_dir)
    print(f"[backfill] 완료: {markets} unit={a.unit}m days={a.days} → {a.cache_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
