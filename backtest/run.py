"""백테스트 실행 진입점 (단일 책임: CLI 조립 → 실행 → 리포트).

예) .venv/Scripts/python -m backtest.run --strategy sma --symbols KRW-BTC,KRW-ETH --start "2026-06-16 00:00:00" --out runs/sma_base
ClickHouse(Docker)가 떠 있고 ticks에 과거 데이터가 있어야 한다.
"""
import argparse
import subprocess
import sys
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
)
from backtest.account import BacktestAccount
from backtest.datasource import load_ticks
from backtest.engine import BacktestEngine
from backtest.fills import FillModel
from backtest.metrics import compute_metrics
from backtest.report import print_summary, write_outputs
from strategy.registry import available, get_strategy


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="전략 백테스트 (ClickHouse 틱 replay)")
    p.add_argument("--strategy", default="sma", help=f"전략 이름 {available()}")
    p.add_argument("--symbols", default="", help="쉼표 구분(미지정=전체)")
    p.add_argument("--start", default="", help="UTC 시작 (예: '2026-06-16 00:00:00')")
    p.add_argument("--end", default="", help="UTC 끝(미포함)")
    p.add_argument("--initial", default=str(INITIAL_BALANCE), help="초기 가상자금(KRW)")
    p.add_argument("--fee", default=str(FEE_RATE), help="수수료율(기본=config FEE_RATE)")
    p.add_argument("--slippage-bps", default="0", help="불리한 슬리피지(bps, 기본 0=라이브 가정)")
    p.add_argument("--sample-sec", type=float, default=60.0, help="자산곡선 표본 간격(초)")
    p.add_argument("--no-final", action="store_true", help="ClickHouse FINAL 생략(중복 정리 안 함)")
    p.add_argument("--out", default="", help="결과 CSV/메타 저장 디렉터리")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    try:
        initial = Decimal(args.initial)
        fills = FillModel(fee_rate=Decimal(args.fee), slippage_bps=Decimal(args.slippage_bps))
        strategy = get_strategy(args.strategy)
    except (InvalidOperation, ValueError) as e:
        print(f"[backtest] 잘못된 인자(--initial/--fee/--slippage-bps/--strategy): {e}", file=sys.stderr)
        return 2

    account = BacktestAccount(initial)
    engine = BacktestEngine(account, fills, equity_sample_sec=args.sample_sec)

    try:
        ticks = load_ticks(symbols=symbols or None, start=args.start or None,
                           end=args.end or None, use_final=not args.no_final)
        engine.run(ticks, strategy)
    except Exception as e:  # ClickHouse 연결 실패 등
        print(f"[backtest] 데이터 로드 실패: {e}", file=sys.stderr)
        print("[backtest] Docker(ClickHouse)가 떠 있는지, ticks에 데이터가 있는지 확인하세요.", file=sys.stderr)
        return 2

    if engine.n_ticks == 0:
        print("[backtest] 0틱 — 기간/심볼/데이터 적재 여부를 확인하세요.", file=sys.stderr)
        return 1

    metrics = compute_metrics(engine.closed_trades, engine.equity_curve, initial,
                              engine.final_equity, mdd_override=engine.max_drawdown,
                              sample_sec=args.sample_sec)
    meta = {
        "strategy": strategy.name,
        "symbols": symbols or None,
        "start": args.start or None,
        "end": args.end or None,
        "fee_rate": str(fills.fee_rate),
        "slippage_bps": str(fills.slippage_bps),
        "sample_sec": args.sample_sec,
        "git_commit": _git_commit(),
        "strategy_params": {
            "SMA_SHORT": SMA_SHORT, "SMA_LONG": SMA_LONG,
            "ENTRY_BAND": str(STRATEGY_ENTRY_BAND), "CONFIRM_TICKS": STRATEGY_CONFIRM_TICKS,
            "STRONG_GAP": str(STRATEGY_STRONG_GAP),
            "ORDER_FRACTION_MIN": str(STRATEGY_ORDER_FRACTION_MIN),
            "ORDER_FRACTION_MAX": str(STRATEGY_ORDER_FRACTION_MAX),
            "STOP_LOSS_PCT": str(STRATEGY_STOP_LOSS_PCT), "TAKE_PROFIT_PCT": str(STRATEGY_TAKE_PROFIT_PCT),
            "TRAIL_ARM_PCT": str(STRATEGY_TRAIL_ARM_PCT), "TRAIL_GIVEBACK_PCT": str(STRATEGY_TRAIL_GIVEBACK_PCT),
            "COOLDOWN_SEC": STRATEGY_COOLDOWN_SEC, "MIN_HOLD_SEC": STRATEGY_MIN_HOLD_SEC,
            "WARMUP_SEC": STRATEGY_WARMUP_SEC, "MAX_POSITIONS": STRATEGY_MAX_POSITIONS,
        },
    }
    print_summary(metrics, meta, engine)
    if args.out:
        write_outputs(args.out, engine, metrics, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
