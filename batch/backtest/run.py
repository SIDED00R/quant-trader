"""백테스트 실행 진입점 (단일 책임: CLI 조립 → 실행 → 리포트).

예) 캐시(업비트 REST 백필본)로 2년 백테스트:
  .venv/Scripts/python -m batch.backtest.run --source upbit --days 730 --sample-sec 86400 --out runs/sma_base
예) ClickHouse candles_1m로 백테스트:
  .venv/Scripts/python -m batch.backtest.run --source clickhouse --symbols KRW-BTC --start "2026-06-01 00:00:00"
예) 주식 일봉(토스 적재본)으로 백테스트:
  .venv/Scripts/python -m batch.backtest.run --source clickhouse --ch-table stock_candles_1d --symbols 005930 --strategy ensemble --sample-sec 86400
업비트 소스는 사전 백필 필요: python -m batch.backtest.backfill --days 730 --symbols KRW-BTC,KRW-ETH
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from common.config import (
    FEE_RATE,
    INITIAL_BALANCE,
    SMA_LONG,
    SMA_SHORT,
    STRATEGY_CONFIRM_TICKS,
    STRATEGY_COOLDOWN_SEC,
    STRATEGY_ENTRY_BAND,
    STRATEGY_MAX_POSITIONS,
    STRATEGY_MIN_HOLD_SEC,
    STRATEGY_ORDER_FRACTION_MAX,
    STRATEGY_ORDER_FRACTION_MIN,
    STRATEGY_STOP_LOSS_PCT,
    STRATEGY_STRONG_GAP,
    STRATEGY_TAKE_PROFIT_PCT,
    STRATEGY_TRAIL_ARM_PCT,
    STRATEGY_TRAIL_GIVEBACK_PCT,
    STRATEGY_WARMUP_SEC,
    SYMBOLS,
    TREND_BARS_PER_YEAR,
    TREND_ENTRY_BAND,
    TREND_LONG,
    TREND_MAX_WEIGHT,
    TREND_REGIME_MAX_VOL,
    TREND_SHORT,
    TREND_VOL_LOOKBACK,
    TREND_VOL_TARGET,
)
from batch.backtest.account import BacktestAccount
from batch.backtest.datasource import load_clickhouse_candles
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.metrics import compute_metrics
from batch.backtest.report import print_summary, write_outputs
from batch.backtest.upbit_candles import load as load_candle_cache
from common.market_hours import is_market_open, periods_per_year
from trading.strategy.registry import available, get_strategy


def _regular_session_only(candles):
    """정규장 봉만 통과(주식 분봉의 시간외 봉 제외). market_hours 기준(KR 09:00–15:30 KST / US 09:30–16:00 ET)."""
    for t in candles:
        if is_market_open(t.symbol, datetime.fromtimestamp(t.ts, timezone.utc)):
            yield t


def _strategy_params(name: str) -> dict:
    """전략별 재현에 필요한 파라미터만 기록(비SMA 전략에 SMA 파라미터를 섞지 않기 위해 분기)."""
    common = {
        "STOP_LOSS_PCT": str(STRATEGY_STOP_LOSS_PCT), "TAKE_PROFIT_PCT": str(STRATEGY_TAKE_PROFIT_PCT),
        "TRAIL_ARM_PCT": str(STRATEGY_TRAIL_ARM_PCT), "TRAIL_GIVEBACK_PCT": str(STRATEGY_TRAIL_GIVEBACK_PCT),
        "COOLDOWN_SEC": STRATEGY_COOLDOWN_SEC, "MIN_HOLD_SEC": STRATEGY_MIN_HOLD_SEC,
        "WARMUP_SEC": STRATEGY_WARMUP_SEC, "MAX_POSITIONS": STRATEGY_MAX_POSITIONS,
        "ORDER_FRACTION_MAX": str(STRATEGY_ORDER_FRACTION_MAX),
    }
    if name == "sma":
        common.update({
            "SMA_SHORT": SMA_SHORT, "SMA_LONG": SMA_LONG,
            "ENTRY_BAND": str(STRATEGY_ENTRY_BAND), "CONFIRM_TICKS": STRATEGY_CONFIRM_TICKS,
            "STRONG_GAP": str(STRATEGY_STRONG_GAP),
            "ORDER_FRACTION_MIN": str(STRATEGY_ORDER_FRACTION_MIN),
        })
    elif name == "trend":  # 저회전 추세 전략은 봉 기반 가드라 위 초 단위 공통 가드와 무관 — 자체 파라미터만 기록
        return {
            "TREND_SHORT": TREND_SHORT, "TREND_LONG": TREND_LONG,
            "TREND_ENTRY_BAND": str(TREND_ENTRY_BAND), "TREND_VOL_TARGET": str(TREND_VOL_TARGET),
            "TREND_VOL_LOOKBACK": TREND_VOL_LOOKBACK, "TREND_MAX_WEIGHT": str(TREND_MAX_WEIGHT),
            "TREND_REGIME_MAX_VOL": str(TREND_REGIME_MAX_VOL), "TREND_BARS_PER_YEAR": TREND_BARS_PER_YEAR,
        }
    return common


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="전략 백테스트 (분봉 replay)")
    p.add_argument("--strategy", default="sma", help=f"전략 이름 {available()}")
    p.add_argument("--source", default="upbit", choices=["upbit", "clickhouse"],
                   help="upbit=REST 백필 캐시 / clickhouse=candles_1m·1d")
    p.add_argument("--ch-table", default="candles_1m",
                   choices=["candles_1m", "candles_1d", "stock_candles_1d", "stock_candles_1m"],
                   help="clickhouse 소스 테이블(1d=코인 장기 일봉, stock_candles_1d=주식 일봉, stock_candles_1m=주식 분봉)")
    p.add_argument("--symbols", default="", help="쉼표 구분(upbit는 미지정 시 config SYMBOLS, clickhouse는 전체)")
    p.add_argument("--unit", type=int, default=1, help="분봉 단위(upbit 캐시 unit과 일치)")
    p.add_argument("--bar-min", type=int, default=0,
                   help="상위 타임프레임 리샘플 분(0=원본 unit, 예 1440=일봉). 신호·거래 빈도 축소")
    p.add_argument("--days", type=int, default=730, help="upbit: 최근 N일 (기본 2년)")
    p.add_argument("--cache-dir", default="data/candles", help="upbit 캐시 디렉터리")
    p.add_argument("--start", default="", help="clickhouse: UTC 시작 (예: '2026-06-16 00:00:00')")
    p.add_argument("--end", default="", help="clickhouse: UTC 끝(미포함)")
    p.add_argument("--initial", default=str(INITIAL_BALANCE), help="초기 가상자금(KRW)")
    p.add_argument("--fee", default=str(FEE_RATE), help="수수료율(기본=config FEE_RATE)")
    p.add_argument("--slippage-bps", default="0", help="불리한 슬리피지(bps, 기본 0=라이브 가정)")
    p.add_argument("--sample-sec", type=float, default=60.0, help="자산곡선 표본 간격(초, 장기엔 86400 권장)")
    p.add_argument("--all-hours", action="store_true",
                   help="주식 분봉(stock_candles_1m)의 정규장 외 봉도 포함(기본: 정규장만)")
    p.add_argument("--out", default="", help="결과 CSV/메타 저장 디렉터리")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:  # 한글/기호 출력이 cp949 콘솔에서 깨지거나 크래시하지 않도록 UTF-8 강제(stdout=리포트, stderr=진단)
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    try:
        initial = Decimal(args.initial)
        fills = FillModel(fee_rate=Decimal(args.fee), slippage_bps=Decimal(args.slippage_bps))
        strategy = get_strategy(args.strategy)
    except (InvalidOperation, ValueError) as e:
        print(f"[backtest] 잘못된 인자(--initial/--fee/--slippage-bps/--strategy): {e}", file=sys.stderr)
        return 2

    if hasattr(strategy, "configure"):   # 횡단면 전략: 데이터 봉 간격(분) 주입 — 봉키 정규화 기준
        bar_min = (args.bar_min or args.unit) if args.source == "upbit" else (
            1 if args.ch_table in ("candles_1m", "stock_candles_1m") else 1440)
        strategy.configure(bar_min)

    account = BacktestAccount(initial)
    engine = BacktestEngine(account, fills, equity_sample_sec=args.sample_sec)

    try:
        if args.source == "upbit":
            markets = symbols or SYMBOLS
            start_ms = int((time.time() - args.days * 86400) * 1000) if args.days > 0 else None
            candles = load_candle_cache(markets, args.unit, args.cache_dir, start_ms=start_ms,
                                        bar_min=args.bar_min or None)
        else:
            candles = load_clickhouse_candles(symbols=symbols or None,
                                              start=args.start or None, end=args.end or None,
                                              table=args.ch_table)
            if args.ch_table == "stock_candles_1m" and not args.all_hours:
                candles = _regular_session_only(candles)   # 시간외 봉 제외(정규장만)
        engine.run(candles, strategy)
    except Exception as e:
        print(f"[backtest] 데이터 로드 실패: {e}", file=sys.stderr)
        return 2

    if engine.n_bars == 0:
        if args.source == "upbit":
            print("[backtest] 0봉 — 캐시가 비었습니다. 먼저 백필: "
                  "python -m batch.backtest.backfill --days {} --unit {}".format(args.days, args.unit), file=sys.stderr)
        else:
            print(f"[backtest] 0봉 — ClickHouse {args.ch_table}/기간/심볼을 확인하세요.", file=sys.stderr)
        return 1

    rep = symbols[0] if symbols else ("000000" if args.ch_table.startswith("stock") else "KRW-BTC")
    ppy = periods_per_year(rep, args.sample_sec)   # 자산군 인지 연율화(코인 무영향, 주식 252×세션)
    metrics = compute_metrics(engine.closed_trades, engine.equity_curve, initial,
                              engine.final_equity, mdd_override=engine.max_drawdown,
                              sample_sec=args.sample_sec, periods_per_year=ppy)
    meta = {
        "strategy": strategy.name,
        "source": args.source,
        "unit_min": args.unit if args.source == "upbit" else 1,  # clickhouse는 candles_1m(1분) 고정
        "bar_min": args.bar_min or None,                          # 리샘플 타임프레임(None=원본)
        "symbols": symbols or (SYMBOLS if args.source == "upbit" else None),
        "days": args.days if args.source == "upbit" else None,
        "start": args.start or None,
        "end": args.end or None,
        "fee_rate": str(fills.fee_rate),
        "slippage_bps": str(fills.slippage_bps),
        "sample_sec": args.sample_sec,
        "periods_per_year": ppy,
        "git_commit": _git_commit(),
        "strategy_params": _strategy_params(strategy.name),
    }
    print_summary(metrics, meta, engine)
    if args.out:
        write_outputs(args.out, engine, metrics, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
