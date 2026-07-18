"""부하 재평가 잡 (단일 책임: 부하별 OOS 성과 → strategy_weights 갱신).

수동 전용 잡(스케줄 미배선). 각 추세 부하(default_loads: 5/40·10/60·20/100)를 candles_1d로 walk-forward OOS 평가해
위험조정성과(스코어=OOS Sharpe)와 유의도(게이트=DSR, 고정구성이라 N=1 → PSR)를 구하고, 보수적 정책
(weight_policy.compute_weights: floor/cap·EWMA·DSR게이트·demote≠delete)으로 새 가중치를 산출·UPSERT한다.

**실행 환경 주의**: backtest(walkforward/datasource) + ClickHouse 의존 → 라이브 prod 이미지가 아닌 별도
backtest 이미지/배치로 실행한다(라이브 commander는 strategy_weights를 읽기만 함).
잡이 테이블을 갱신해도 commander는 ENSEMBLE_ADAPTIVE를 켜야 반영하므로, 적응 활성 전 A/B·검증 단계에서 안전하게 돌릴 수 있다.
"""
import argparse
import sys
from decimal import Decimal

from common.config import (
    ENSEMBLE_DSR_GATE,
    ENSEMBLE_SYMBOLS,
    ENSEMBLE_WEIGHT_CAP_MULT,
    ENSEMBLE_WEIGHT_EWMA,
    ENSEMBLE_WEIGHT_FLOOR_MULT,
    FEE_RATE,
    INITIAL_BALANCE,
)
from common.postgres_client import close_pool, open_pool, pool
from trading.strategy.plugins.ensemble import default_loads
from trading.strategy.core.weight_policy import compute_weights

_DAY = 86400.0


def score_loads(bars, loads, initial, fills, sample_sec, is_sec, oos_sec, step_sec):
    """각 부하의 walk-forward OOS 성과 → {load: (score, gate)}. score=연율 Sharpe, gate=DSR(N=1→PSR)."""
    from batch.backtest.metrics import SECONDS_PER_YEAR, _sharpe_from_returns, deflated_sharpe
    from batch.backtest.walkforward import oos_returns
    from trading.strategy.plugins.trend import TrendStrategy

    ppy = SECONDS_PER_YEAR / sample_sec if sample_sec > 0 else SECONDS_PER_YEAR
    # 공통 prime = 부하 중 최장 warmup → 모든 부하를 동일 OOS 창에서 평가(부하 간 Sharpe 공정 비교).
    prime_bars = max(TrendStrategy(short=s, long=l).warmup_bars for _n, s, l in loads)
    out = {}
    for name, short, long in loads:
        rets = oos_returns(bars, lambda s=short, l=long: TrendStrategy(short=s, long=l),
                           prime_bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec)
        gate = deflated_sharpe(rets, 0.0, 1)        # 고정구성 → N=1 → benchmark 0의 PSR(=엣지 유의확률)
        out[name] = (_sharpe_from_returns(rets, ppy), gate if gate is not None else 0.0)
    return out


def _load_prev(names):
    """strategy_weights에서 직전 가중치 {load: weight} 조회(EWMA 입력). 없으면 빈 dict."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT strategy, weight FROM strategy_weights WHERE strategy = ANY(%s)", (list(names),)
        ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _save(weights):
    """{load: weight} UPSERT(updated_at 갱신)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO strategy_weights (strategy, weight, updated_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (strategy) DO UPDATE SET weight = EXCLUDED.weight, updated_at = now()",
                [(name, Decimal(str(round(w, 6)))) for name, w in weights.items()],
            )
        conn.commit()


def reeval(bars, loads, initial, fills, sample_sec, is_sec, oos_sec, step_sec, *, dry_run=False, log=print):
    """부하 평가 → 정책 적용 → (dry_run이 아니면) UPSERT. 산출 가중치 dict 반환."""
    scored = score_loads(bars, loads, initial, fills, sample_sec, is_sec, oos_sec, step_sec)
    scores = {k: v[0] for k, v in scored.items()}
    gates = {k: v[1] for k, v in scored.items()}
    prev = _load_prev(scores.keys())
    weights = compute_weights(scores, gates, prev,
                              floor_mult=ENSEMBLE_WEIGHT_FLOOR_MULT, cap_mult=ENSEMBLE_WEIGHT_CAP_MULT,
                              dsr_gate=ENSEMBLE_DSR_GATE, ewma_alpha=ENSEMBLE_WEIGHT_EWMA)
    for name in scores:
        demoted = "" if gates[name] >= ENSEMBLE_DSR_GATE else "  [DSR<게이트→강등]"
        log(f"[reeval] {name}: Sharpe={scores[name]:+.2f} DSR={gates[name]:.3f} "
            f"→ weight={weights[name]:.4f}{demoted}")
    if dry_run:
        log("[reeval] dry-run — strategy_weights 미갱신")
    else:
        _save(weights)
        log(f"[reeval] strategy_weights {len(weights)}건 갱신 완료")
    return weights


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="부하 재평가 → strategy_weights 갱신 (5.4)")
    p.add_argument("--symbols", default="", help="쉼표 구분(미지정 시 ENSEMBLE_SYMBOLS)")
    p.add_argument("--ch-table", default="candles_1d", choices=["candles_1m", "candles_1d"])
    p.add_argument("--is-days", type=int, default=180, help="IS 일수(fold당)")
    p.add_argument("--oos-days", type=int, default=90, help="OOS 일수(fold당)")
    p.add_argument("--step-days", type=int, default=90, help="fold 전진 간격")
    p.add_argument("--initial", default=str(INITIAL_BALANCE), help="평가용 초기 가상자금")
    p.add_argument("--fee", default=str(FEE_RATE), help="수수료율")
    p.add_argument("--dry-run", action="store_true", help="계산만 하고 DB 미갱신")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from batch.backtest.datasource import load_clickhouse_candles
    from batch.backtest.fills import FillModel

    args = parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or ENSEMBLE_SYMBOLS
    initial, fills = Decimal(args.initial), FillModel(fee_rate=Decimal(args.fee))
    sample_sec = 86400.0 if args.ch_table == "candles_1d" else 60.0
    bars = list(load_clickhouse_candles(symbols=symbols, table=args.ch_table))
    if not bars:
        print("[reeval] 0봉 — 먼저 백필: python -m batch.backtest.backfill_daily --symbols KRW-BTC,KRW-ETH",
              file=sys.stderr)
        return 1
    open_pool()
    try:
        reeval(bars, default_loads(), initial, fills, sample_sec,
               args.is_days * _DAY, args.oos_days * _DAY, args.step_days * _DAY, dry_run=args.dry_run)
    finally:
        close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
