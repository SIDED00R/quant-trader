"""모델 평가 (단일 책임: OOF 예측 → Rank IC/ICIR/NW-t + 롱숏 성과).

RMSE 금지(과제는 랭킹). 1차: 일별 횡단면 Rank IC → 평균·ICIR·Newey-West t(겹침보정).
2차: 점수 상/하위 분위 롱숏 스프레드 → 연율 Sharpe(비겹침 근사, 모델 간 비교용 일관 계산).
"""
import numpy as np
import pandas as pd

MIN_SYMS = 30


def _nw_t(x: np.ndarray, lag: int) -> float:
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 20:
        return np.nan
    d = x - x.mean()
    s = (d @ d) / n
    for L in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - L / (lag + 1)) * (d[L:] @ d[:-L]) / n
    se = np.sqrt(s / n)
    return x.mean() / se if se > 0 else np.nan


def daily_rank_ic(df: pd.DataFrame) -> pd.Series:
    """df[date,pred,fwd_ret] → 일별 횡단면 Rank IC(Spearman=rank 후 pearson)."""
    sub = df.dropna(subset=["pred", "fwd_ret"])
    return sub.groupby("date").apply(
        lambda g: g["pred"].rank().corr(g["fwd_ret"].rank()) if len(g) >= MIN_SYMS else np.nan,
        include_groups=False).dropna()


def long_short(df: pd.DataFrame, q: float = 0.1, horizon: int = 21) -> dict:
    """점수 상위 q − 하위 q 롱숏 스프레드 → 평균·연율 Sharpe(비겹침 근사)."""
    sub = df.dropna(subset=["pred", "fwd_ret"])

    def spread(g):
        if len(g) < MIN_SYMS:
            return np.nan
        k = max(1, int(len(g) * q))
        s = g.sort_values("pred")
        return s["fwd_ret"].iloc[-k:].mean() - s["fwd_ret"].iloc[:k].mean()

    sp = sub.groupby("date").apply(spread, include_groups=False).dropna()
    if len(sp) < 20:
        return {"ls_mean": np.nan, "ls_sharpe": np.nan, "n": len(sp)}
    ppy = 252.0 / horizon
    sharpe = sp.mean() / (sp.std() + 1e-12) * np.sqrt(ppy)
    return {"ls_mean": sp.mean(), "ls_sharpe": sharpe, "n": len(sp)}


def summarize(df: pd.DataFrame, horizon: int = 21, label: str = "") -> dict:
    """OOF df[date,pred,fwd_ret] → 종합 지표."""
    ic = daily_rank_ic(df)
    arr = ic.to_numpy()
    m, sd = (arr.mean(), arr.std()) if len(arr) else (np.nan, np.nan)
    ls = long_short(df, horizon=horizon)
    return {"label": label, "mean_ic": m, "icir": m / sd if sd else np.nan,
            "nw_t": _nw_t(arr, horizon - 1), "ic_days": len(arr),
            "ls_sharpe": ls["ls_sharpe"], "ls_mean": ls["ls_mean"]}


def print_summary(rows: list):
    print(f"{'model':22}{'meanIC':>9}{'ICIR':>8}{'NW_t':>7}{'LS_Sharpe':>11}{'LS_mean':>9}{'days':>7}")
    print("-" * 73)
    for r in rows:
        print(f"{r['label']:22}{r['mean_ic']*100:>8.2f}%{r['icir']:>8.3f}{r['nw_t']:>7.1f}"
              f"{r['ls_sharpe']:>11.2f}{r['ls_mean']*100:>8.2f}%{int(r['ic_days']):>7}")
