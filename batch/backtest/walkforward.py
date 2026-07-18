"""Walk-forward 백테스트 러너 (단일 책임: 롤링 IS/OOS 평가 + Deflated Sharpe 보고).

과적합·룩어헤드를 피하려 전체데이터 일괄 측정 대신 **롤링 윈도우**로 평가한다:
각 fold마다 IS(in-sample)에서 파라미터 그리드 중 최선을 고르고, 그 파라미터로 **직후 OOS(out-of-sample)**
구간 성과만 집계한다. fold마다 계좌를 새로 시작하고, OOS 직전 prime 구간으로 지표를 priming해(거래 억제)
워밍업으로 OOS 데이터를 잃지 않으면서 룩어헤드를 차단한다.

다중 시도(그리드 N개) 선택편향은 **Deflated Sharpe**로 보정해 OOS 합성수익의 유의도를 보고한다.
대상은 저회전 추세 전략(strategy/trend.py) — 상위 타임프레임(--bar-min, 기본 일봉)에서 평가한다.
"""
import argparse
import statistics
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from common.config import FEE_RATE, INITIAL_BALANCE, SYMBOLS
from common.marketdata.market_hours import is_market_open, periods_per_year as ppy_fn
from batch.backtest.account import BacktestAccount
from batch.backtest.datasource import load_clickhouse_candles
from batch.backtest.engine import BacktestEngine
from batch.backtest.fills import FillModel
from batch.backtest.metrics import SECONDS_PER_YEAR, _sharpe_from_returns, deflated_sharpe
from batch.backtest.upbit_candles import load as load_candle_cache
from trading.strategy.ensemble import EnsembleStrategy
from trading.strategy.trend import TrendStrategy

_GRID_SHORT = [5, 10, 20]
_GRID_LONG = [30, 40, 60]
_DAY = 86400.0


def _combos():
    """(short, long) 파라미터 그리드 — short < long 만. 시도 수 N = len(combos)."""
    return [(s, l) for s in _GRID_SHORT for l in _GRID_LONG if s < l]


class _NullBroker:
    """priming 전용 broker — 워밍업 구간 지표만 채우고 거래는 일절 집행하지 않는다(전량 현금·flat).

    예산이 0이라 _enter가 매수 시도조차 안 하고, position_qty=0이라 청산도 없다 → prime 무거래를 구조적으로 보장.
    """
    def position_qty(self, s): return Decimal(0)
    def position_avg(self, s): return Decimal(0)
    def cash(self): return Decimal(0)
    def equity(self): return Decimal(0)
    def open_symbol_count(self): return 0
    def iter_positions(self): return []
    def price(self, s): return Decimal(0)
    def buy(self, s, q, ts, price=None): return False
    def sell(self, s, q, r, ts, price=None): return False


def _evaluate(bars, factory, prime_start, eval_start, eval_end, initial, fills, sample_sec):
    """[prime_start, eval_start)로 전략 지표만 priming(무거래), [eval_start, eval_end)만 새 계좌로 평가.

    priming은 NullBroker로 구동해 prime 구간에서 어떤 거래도 발생하지 않음을 구조적으로 보장한다
    (워밍업 봉수 vs prime 길이 불일치로 인한 OOS 오염을 원천 차단). OOS는 새 계좌(initial)로만 replay하므로
    모든 거래·평가손익이 OOS 귀속이다. 반환: 수익률·봉별 수익률(rets)·거래수·수수료.
    """
    strat = factory()
    null = _NullBroker()
    for t in bars:
        if prime_start <= t.ts < eval_start:
            strat.on_tick(t, null)             # 지표 priming(무거래)
    oos = [t for t in bars if eval_start <= t.ts < eval_end]
    account = BacktestAccount(initial)
    engine = BacktestEngine(account, fills, equity_sample_sec=sample_sec)
    engine.run(oos, strat)                      # priming된 전략을 OOS만 새 계좌로 평가
    eq_vals = [float(eq) for _, eq in engine.equity_curve]
    final = eq_vals[-1] if eq_vals else float(initial)
    ret = final / float(initial) - 1.0         # 계좌가 OOS에서 새로 시작 → 기준은 initial(prime 무활동 보장)
    rets = [eq_vals[i] / eq_vals[i - 1] - 1.0 for i in range(1, len(eq_vals)) if eq_vals[i - 1] > 0]
    fees = sum((t.buy_fee + t.sell_fee for t in engine.closed_trades), Decimal(0))
    tax = sum((t.sell_tax for t in engine.closed_trades), Decimal(0))  # 매도 거래세(국내주식만 >0, 코인=0)
    return {"return": ret, "rets": rets, "num_trades": len(engine.closed_trades), "fees": fees, "tax": tax}


def _folds(t0, t1, prime_sec, is_sec, oos_sec, step_sec):
    """롤링 fold 경계 생성. 각 fold: (is_prime, is_start, oos_prime, oos_start, oos_end)."""
    folds = []
    oos_start = t0 + prime_sec + is_sec   # 첫 OOS는 prime+IS 확보 후
    while oos_start + oos_sec <= t1:
        folds.append((
            oos_start - is_sec - prime_sec,  # is_prime_start
            oos_start - is_sec,              # is_start (IS 거래 구간 시작)
            oos_start - prime_sec,           # oos_prime_start (= IS 꼬리)
            oos_start,                       # oos_start
            oos_start + oos_sec,             # oos_end
        ))
        oos_start += step_sec
    return folds


def _aggregate(fold_results, combined_oos_rets, sample_sec, n_trials, sr_var, periods_per_year=None):
    """fold별 OOS 결과 → 집계 dict(합성수익·양수fold·OOS Sharpe·Deflated/Probabilistic Sharpe)."""
    # periods_per_year 명시되면 자산군 인지 연율화(주식 252×세션). 미지정 시 24/7 기준(코인).
    ppy = periods_per_year if periods_per_year is not None else (
        SECONDS_PER_YEAR / sample_sec if sample_sec > 0 else SECONDS_PER_YEAR)
    compound = 1.0
    for fr in fold_results:
        compound *= (1.0 + fr["return"])
    return {
        "oos_folds": len(fold_results),
        "positive_oos_folds": sum(1 for fr in fold_results if fr["return"] > 0),
        "oos_compound_return": compound - 1.0,
        "oos_mean_return": statistics.mean([fr["return"] for fr in fold_results]) if fold_results else 0.0,
        "oos_total_trades": sum(fr["num_trades"] for fr in fold_results),
        "oos_total_fees": sum((fr["fees"] for fr in fold_results), Decimal(0)),
        "oos_total_tax": sum((fr["tax"] for fr in fold_results), Decimal(0)),

        "combined_oos_sharpe": _sharpe_from_returns(combined_oos_rets, ppy),
        "n_trials": n_trials,
        "sr_variance": sr_var,
        "deflated_sharpe": deflated_sharpe(combined_oos_rets, sr_var, n_trials),
    }


def _run_fixed(bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec, factory, name, prime_bars,
               log, periods_per_year=None):
    """고정 구성(IS 파라미터 선택 없음) walk-forward — 임의 전략 factory의 각 OOS만 평가(n_trials=1 → PSR)."""
    folds = _folds(bars[0].ts, bars[-1].ts, prime_bars * sample_sec, is_sec, oos_sec, step_sec)
    if not folds:
        return {"folds": [], "error": "기간이 짧아 fold 없음(IS+OOS+prime > 데이터)"}
    fold_results, combined_oos_rets = [], []
    for i, (_isp, _iss, oos_prime, oos_start, oos_end) in enumerate(folds, 1):
        oosr = _evaluate(bars, factory, oos_prime, oos_start, oos_end, initial, fills, sample_sec)
        if not oosr["rets"]:
            log(f"[wf] fold {i}: OOS 표본 부족(스킵)")
            continue
        combined_oos_rets.extend(oosr["rets"])
        fold_results.append({"fold": i, "params": None, "is_return": None, **oosr})
        log(f"[wf] fold {i}: {name} OOS {oosr['return']*100:+.1f}% ({oosr['num_trades']}거래)")
    agg = _aggregate(fold_results, combined_oos_rets, sample_sec, 1, 0.0, periods_per_year)  # 고정구성 → N=1(PSR)
    return {"folds": fold_results, "aggregate": agg, "strategy": name}


def _run_ensemble(bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec, log, periods_per_year=None):
    """앙상블(고정 구성) walk-forward."""
    prime_bars = max(s.warmup_bars for s in EnsembleStrategy().signals)
    return _run_fixed(bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec,
                      lambda: EnsembleStrategy(), "ensemble", prime_bars, log, periods_per_year)


def run_walkforward(bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec, strategy="trend", log=print,
                    periods_per_year=None, factory=None):
    """walk-forward 실행 → fold별 OOS 성과 + 집계.

    strategy='trend'(그리드 선택) / 'ensemble'(고정) / 그 외=임의 등록 전략(고정 구성, factory 필수).
    """
    if not bars:
        return {"folds": [], "error": "no bars"}
    if strategy == "ensemble":
        return _run_ensemble(bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec, log, periods_per_year)
    if strategy != "trend":                  # 임의 등록 전략(고정 구성) — factory가 fold별 새 인스턴스 생성
        if factory is None:
            return {"folds": [], "error": f"strategy='{strategy}'에는 factory가 필요합니다"}
        prime_bars = getattr(factory(), "warmup_bars", 1)
        return _run_fixed(bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec,
                          factory, strategy, prime_bars, log, periods_per_year)
    combos = _combos()
    # prime 길이 = 그리드 전 조합 중 최장 워밍업(전략의 실제 warmup_bars를 직접 읽음 — 공식 발산 방지)
    prime_bars = max(TrendStrategy(short=s, long=l).warmup_bars for s, l in combos)
    prime_sec = prime_bars * sample_sec
    t0, t1 = bars[0].ts, bars[-1].ts
    folds = _folds(t0, t1, prime_sec, is_sec, oos_sec, step_sec)
    if not folds:
        return {"folds": [], "error": "기간이 짧아 fold 없음(IS+OOS+prime > 데이터)"}

    fold_results = []
    combined_oos_rets: list[float] = []
    for i, (is_prime, is_start, oos_prime, oos_start, oos_end) in enumerate(folds, 1):
        # IS: 그리드 중 IS 수익 최대 조합 선택
        best, best_score = None, None
        for s, l in combos:
            isr = _evaluate(bars, lambda s=s, l=l: TrendStrategy(short=s, long=l),
                            is_prime, is_start, oos_start, initial, fills, sample_sec)
            if best_score is None or isr["return"] > best_score:
                best, best_score = (s, l), isr["return"]
        # OOS: 선택 조합으로 직후 구간만 평가
        s, l = best
        oosr = _evaluate(bars, lambda s=s, l=l: TrendStrategy(short=s, long=l),
                         oos_prime, oos_start, oos_end, initial, fills, sample_sec)
        if not oosr["rets"]:   # OOS 표본<2(수익률 시리즈 없음) — 집계·DSR 양쪽에서 일관 제외
            log(f"[wf] fold {i}: OOS 표본 부족(스킵)")
            continue
        combined_oos_rets.extend(oosr["rets"])
        fold_results.append({"fold": i, "params": best, "is_return": best_score, **oosr})
        log(f"[wf] fold {i}: IS최적 SMA{best[0]}/{best[1]}(IS {best_score*100:+.1f}%) "
            f"→ OOS {oosr['return']*100:+.1f}% ({oosr['num_trades']}거래)")

    # 그리드 각 조합의 전체기간 봉별 Sharpe 분산 → Deflated Sharpe의 다중시도 보정 입력
    full_sharpes = [
        _sharpe_from_returns(_evaluate(bars, lambda s=s, l=l: TrendStrategy(short=s, long=l),
                                       t0, t0, t1, initial, fills, sample_sec)["rets"])
        for s, l in combos
    ]
    sr_var = statistics.pvariance(full_sharpes) if len(full_sharpes) >= 2 else 0.0
    agg = _aggregate(fold_results, combined_oos_rets, sample_sec, len(combos), sr_var, periods_per_year)
    return {"folds": fold_results, "aggregate": agg, "strategy": "trend"}


def oos_returns(bars, factory, prime_bars, initial, fills, sample_sec, is_sec, oos_sec, step_sec):
    """고정 전략(factory)의 walk-forward 결합 OOS 봉별 수익률 시퀀스(IS 파라미터 선택 없음).

    각 fold의 OOS 구간만 새 계좌로 평가(직전 prime 구간으로 지표 priming, 무거래)하고 봉별 수익률을 이어붙인다.
    단일 (short,long) 부하의 최근 OOS 위험조정성과 입력 — 5.4 재평가 잡의 부하별 스코어링에 사용.
    """
    if not bars:
        return []
    folds = _folds(bars[0].ts, bars[-1].ts, prime_bars * sample_sec, is_sec, oos_sec, step_sec)
    rets: list[float] = []
    for (_isp, _iss, oos_prime, oos_start, oos_end) in folds:
        rets.extend(_evaluate(bars, factory, oos_prime, oos_start, oos_end, initial, fills, sample_sec)["rets"])
    return rets


def _print(result):
    if result.get("error"):
        print(f"[wf] {result['error']}", file=sys.stderr)
        return
    a = result["aggregate"]
    name = "앙상블(다중 추세속도)" if result.get("strategy") == "ensemble" else "저회전 추세 전략"
    print("=" * 60)
    print(f"  Walk-forward ({name}, 롤링 IS/OOS)")
    print("=" * 60)
    print(f"  OOS fold 수      : {a['oos_folds']}  (양수 {a['positive_oos_folds']})")
    print(f"  OOS 합성수익률   : {a['oos_compound_return']*100:+.2f}%")
    print(f"  OOS 평균(폴드)   : {a['oos_mean_return']*100:+.2f}%")
    print(f"  OOS 총거래/수수료: {a['oos_total_trades']}건 / {a['oos_total_fees']:,.0f} KRW")
    print(f"  OOS 매도 거래세  : {a['oos_total_tax']:,.0f} KRW")
    print(f"  OOS Sharpe(연율) : {a['combined_oos_sharpe']:.2f}")
    dsr = a["deflated_sharpe"]
    print(f"  Deflated Sharpe  : {dsr:.3f} (시도 N={a['n_trials']})" if dsr is not None
          else f"  Deflated Sharpe  : N/A (시도 N={a['n_trials']})")
    print("-" * 60)
    for fr in result["folds"]:
        label = f"SMA{fr['params'][0]}/{fr['params'][1]:<3}" if fr.get("params") else "앙상블   "
        print(f"  fold {fr['fold']:>2}: {label} OOS {fr['return']*100:+7.2f}%  {fr['num_trades']:>3}거래")
    print("=" * 60)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="walk-forward (롤링 IS/OOS + Deflated/Probabilistic Sharpe)")
    p.add_argument("--strategy", default="trend",
                   help="trend=그리드선택 / ensemble·xs_reversal·xs_momentum·orb·intraday_momentum 등=고정구성(레지스트리 이름)")
    p.add_argument("--symbols", default="", help="쉼표 구분(미지정 시 config SYMBOLS)")
    p.add_argument("--source", default="upbit", choices=["upbit", "clickhouse"],
                   help="upbit=CSV 캐시(리샘플) / clickhouse=candles_1d 장기 일봉")
    p.add_argument("--ch-table", default="candles_1d",
                   choices=["candles_1m", "candles_1d", "stock_candles_1d", "stock_candles_1m"],
                   help="clickhouse 소스 테이블(candles_1d=코인 일봉, stock_candles_1d=주식 일봉, stock_candles_1m=주식 분봉)")
    p.add_argument("--unit", type=int, default=1, help="캐시 분봉 단위(upbit)")
    p.add_argument("--bar-min", type=int, default=1440, help="리샘플 타임프레임 분(upbit, 기본 1440=일봉)")
    p.add_argument("--days", type=int, default=730, help="최근 N일(upbit 캐시 필터)")
    p.add_argument("--cache-dir", default="data/candles", help="upbit 캐시 디렉터리")
    p.add_argument("--is-days", type=int, default=180, help="IS(파라미터 선택) 일수")
    p.add_argument("--oos-days", type=int, default=90, help="OOS(평가) 일수")
    p.add_argument("--step-days", type=int, default=90, help="fold 전진 간격(기본=OOS 길이→연속)")
    p.add_argument("--initial", default=str(INITIAL_BALANCE), help="초기 가상자금(KRW)")
    p.add_argument("--fee", default=str(FEE_RATE), help="수수료율")
    p.add_argument("--all-hours", action="store_true",
                   help="주식 분봉(stock_candles_1m)의 정규장 외 봉도 포함(기본: 정규장만)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or SYMBOLS
    try:
        initial = Decimal(args.initial)
        fills = FillModel(fee_rate=Decimal(args.fee))
    except (InvalidOperation, ValueError) as e:
        print(f"[wf] 잘못된 인자(--initial/--fee): {e}", file=sys.stderr)
        return 2
    try:
        if args.source == "clickhouse":
            bars = list(load_clickhouse_candles(symbols=symbols, table=args.ch_table))
            if args.ch_table == "stock_candles_1m" and not args.all_hours:
                bars = [t for t in bars if is_market_open(t.symbol, datetime.fromtimestamp(t.ts, timezone.utc))]
            sample_sec = 60.0 if args.ch_table in ("candles_1m", "stock_candles_1m") else 86400.0  # 분봉=60·일봉=86400
        else:
            start_ms = int((time.time() - args.days * 86400) * 1000) if args.days > 0 else None
            bars = list(load_candle_cache(symbols, args.unit, args.cache_dir,
                                          start_ms=start_ms, bar_min=args.bar_min or None))
            sample_sec = (args.bar_min or args.unit) * 60.0
    except Exception as e:
        print(f"[wf] 데이터 로드 실패: {e}", file=sys.stderr)
        return 2
    if not bars:
        hint = ("python -m batch.backtest.backfill_daily --symbols KRW-BTC,KRW-ETH" if args.source == "clickhouse"
                else "python -m batch.backtest.backfill --days {} --unit {}".format(args.days, args.unit))
        print(f"[wf] 0봉 — 먼저 백필: {hint}", file=sys.stderr)
        return 1
    factory = None
    if args.strategy not in ("trend", "ensemble"):   # 임의 등록 전략 → 고정구성 factory(fold별 새 인스턴스)
        from trading.strategy.registry import get_strategy
        if args.source == "clickhouse":
            bar_min = 1 if args.ch_table in ("candles_1m", "stock_candles_1m") else 1440
        else:
            bar_min = args.bar_min or args.unit

        def factory():
            s = get_strategy(args.strategy)
            if hasattr(s, "configure"):              # 횡단면/인트라데이: 봉 간격 주입
                s.configure(bar_min)
            return s
        try:
            factory()                                # 미등록 전략 조기 검출
        except ValueError as e:
            print(f"[wf] {e}", file=sys.stderr)
            return 2

    ppy = ppy_fn(symbols[0], sample_sec) if symbols else None   # 자산군 인지 연율화(코인 무영향)
    result = run_walkforward(bars, initial, fills, sample_sec,
                             args.is_days * _DAY, args.oos_days * _DAY, args.step_days * _DAY,
                             strategy=args.strategy, periods_per_year=ppy, factory=factory)
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
