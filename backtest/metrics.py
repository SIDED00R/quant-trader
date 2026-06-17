"""성과지표 (단일 책임: 자산곡선·체결로부터 지표 산출).

핵심: 누적수익률 · MDD · 승률 · 손익비(profit factor) · 거래수 · 평균손익 · (근사)Sharpe.
Sharpe는 표본 자산곡선 수익률 기반의 근사 진단치다(마크투마켓 표본이 균등 간격이 아닐 수 있음).
"""
import math
import statistics
from decimal import Decimal

SECONDS_PER_YEAR = 31_536_000  # 365d (코인 24/7)


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
