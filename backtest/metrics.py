"""성과지표 (단일 책임: 자산곡선·체결로부터 지표 산출).

핵심: 누적수익률 · MDD · 승률 · 손익비(profit factor) · 거래수 · 평균손익 · (근사)Sharpe.
Sharpe는 표본 자산곡선 수익률 기반의 근사 진단치다(마크투마켓 표본이 균등 간격이 아닐 수 있음).
"""
import math
import statistics
from decimal import Decimal

SECONDS_PER_YEAR = 31_536_000  # 365d (코인 24/7)
_EULER_MASCHERONI = 0.5772156649015329  # γ — 극값분포 기대치 보정 상수(Deflated Sharpe)
_NORM = statistics.NormalDist()         # 표준정규 CDF/역CDF (scipy 불요)


def _per_period_sharpe(returns: list[float]) -> float | None:
    """봉별(비연율) Sharpe = 평균/표준편차. 표본<2 또는 표준편차 0이면 None."""
    if len(returns) < 2:
        return None
    sd = statistics.pstdev(returns)
    if sd == 0:
        return None
    return statistics.mean(returns) / sd


def _skew_kurt(returns: list[float]) -> tuple[float, float]:
    """표본 왜도(skew)·첨도(kurtosis, **비초과**: 정규=3.0). 표본<2 또는 표준편차 0이면 (0,3)."""
    n = len(returns)
    if n < 2:
        return 0.0, 3.0
    mean = statistics.mean(returns)
    sd = statistics.pstdev(returns)
    if sd == 0:
        return 0.0, 3.0
    skew = sum(((x - mean) / sd) ** 3 for x in returns) / n
    kurt = sum(((x - mean) / sd) ** 4 for x in returns) / n
    return skew, kurt


def probabilistic_sharpe(sr: float, n: int, skew: float, kurt: float, benchmark: float = 0.0):
    """PSR — 관측 Sharpe(sr, 봉별)가 benchmark를 초과할 확률(Bailey & López de Prado 2014).

    sr·benchmark는 동일 주기(봉별, 비연율). kurt는 비초과 첨도(정규=3). 표본<2/분모<=0이면 None.
    """
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if n < 2 or denom <= 0:
        return None
    z = (sr - benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return _NORM.cdf(z)


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """N회 독립 시도 시 귀무가설 하 기대 최대 Sharpe(봉별). 시도<2/분산<=0이면 0.

    SR0 = √V·[(1-γ)·Z⁻¹(1-1/N) + γ·Z⁻¹(1-1/(N·e))] — 다중시도 선택편향의 기준선.
    """
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    z1 = _NORM.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _NORM.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)


def deflated_sharpe(returns: list[float], sr_variance: float, n_trials: int):
    """DSR — N회 시도로 부풀려진 기준선(expected_max_sharpe) 대비 관측 Sharpe의 PSR.

    returns: OOS 봉별 수익률 시퀀스. sr_variance: 시도들의 Sharpe 분산. n_trials: 시도 수 N.
    N<2면 deflation 불가 → benchmark=0의 PSR(=비편향 유의도)로 보고. 산출 불가 시 None.
    """
    sr = _per_period_sharpe(returns)
    if sr is None:
        return None
    skew, kurt = _skew_kurt(returns)
    sr_star = expected_max_sharpe(sr_variance, n_trials)
    return probabilistic_sharpe(sr, len(returns), skew, kurt, benchmark=sr_star)


def total_return(initial: Decimal, final: Decimal) -> Decimal:
    return (final - initial) / initial if initial > 0 else Decimal(0)


def max_drawdown(values: list[Decimal]) -> Decimal:
    """자산값 시계열의 최대 낙폭(peak 대비 최대 하락 비율). 순수 함수(테스트용)."""
    peak = None
    mdd = Decimal(0)
    for v in values:
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def annualized_sharpe(values: list[float], periods_per_year: float) -> float:
    """표본 자산값 시계열의 연율화 Sharpe(무위험수익 0 가정). 표본<2면 0."""
    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    if sd == 0:
        return 0.0
    return statistics.mean(rets) / sd * math.sqrt(periods_per_year)


def trade_stats(trades: list) -> dict:
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum((t.pnl for t in wins), Decimal(0))
    gross_loss = sum((-t.pnl for t in losses), Decimal(0))
    return {
        "num_trades": n,
        "win_rate": (Decimal(len(wins)) / Decimal(n)) if n else Decimal(0),
        "num_wins": len(wins),
        "num_losses": len(losses),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "avg_win": (gross_profit / len(wins)) if wins else Decimal(0),
        "avg_loss": (gross_loss / len(losses)) if losses else Decimal(0),
        "total_fees": sum((t.buy_fee + t.sell_fee for t in trades), Decimal(0)),
        "avg_holding_sec": (sum(t.holding_sec for t in trades) / n) if n else 0.0,
    }


def compute_metrics(closed_trades, equity_curve, initial_cash, final_equity,
                    mdd_override=None, sample_sec=60.0) -> dict:
    eq_values = [v for _, v in equity_curve]
    mdd = mdd_override if mdd_override is not None else max_drawdown(eq_values)
    periods_per_year = SECONDS_PER_YEAR / sample_sec if sample_sec > 0 else SECONDS_PER_YEAR
    sharpe = annualized_sharpe([float(v) for v in eq_values], periods_per_year)
    m = {
        "initial_equity": initial_cash,
        "final_equity": final_equity,
        "total_return": total_return(initial_cash, final_equity),
        "max_drawdown": mdd,
        "sharpe_annualized": sharpe,
    }
    m.update(trade_stats(closed_trades))
    return m
