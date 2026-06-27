"""피처 유용성 테스트 (단일 책임: 피처 → 미래수익 Rank IC/ICIR/NW t값).

시점별 횡단면 Rank IC(피처 순위 vs 미래 h일 수익 순위)를 측정하고, 겹치는 h일 윈도의
자기상관을 Newey-West로 보정한 t값을 보고한다(겹침 미보정 t는 과대 — 스모크 테스트 교훈).
시장(KR/US) 분리. 외부 데이터 불요(OHLCV만).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.features.ic [US|KR] [--horizon 21]
"""
import argparse
import sys

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
from batch.features.compute import load_ohlcv
from batch.features.ohlcv import compute_features, feature_columns

MIN_SYMS = 30          # 횡단면 IC 계산 최소 종목수


def _forward_return(panel: pd.DataFrame, h: int) -> pd.Series:
    return panel.groupby("symbol")["close"].transform(lambda s: s.shift(-h) / s - 1)


def _nw_tstat(ic: np.ndarray, lag: int) -> float:
    """겹치는 윈도 자기상관을 보정한 평균 IC의 Newey-West t값."""
    ic = ic[~np.isnan(ic)]
    n = len(ic)
    if n < 20:
        return np.nan
    x = ic - ic.mean()
    s = (x @ x) / n
    for L in range(1, min(lag, n - 1) + 1):
        w = 1 - L / (lag + 1)
        s += 2 * w * (x[L:] @ x[:-L]) / n
    se = np.sqrt(s / n)
    return ic.mean() / se if se > 0 else np.nan


def rank_ic(market: str, horizon: int = 21) -> pd.DataFrame:
    panel = load_ohlcv(market)
    feats = compute_features(panel)
    cols = feature_columns(feats)          # fwd 추가 전에 피처 목록 확정(라벨 누설 방지)
    feats["fwd"] = _forward_return(panel.sort_values(["symbol", "date"]).reset_index(drop=True), horizon).values
    fv = feats.dropna(subset=["fwd"])
    res = []
    for ft in cols:
        sub = fv.dropna(subset=[ft])
        ics = sub.groupby("date").apply(
            lambda g: g[ft].rank().corr(g["fwd"].rank()) if len(g) >= MIN_SYMS else np.nan,
            include_groups=False).dropna()
        if len(ics) < 50:
            continue
        arr = ics.to_numpy()
        m, sd = arr.mean(), arr.std()
        res.append({"feature": ft, "mean_ic": m, "icir": m / sd if sd else 0,
                    "pct_pos": (arr > 0).mean(), "nw_t": _nw_tstat(arr, horizon - 1), "n_days": len(arr)})
    return pd.DataFrame(res).sort_values("mean_ic", key=lambda s: s.abs(), ascending=False)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="OHLCV 피처 Rank IC 유용성 테스트")
    p.add_argument("markets", nargs="*", default=["US", "KR"])
    p.add_argument("--horizon", type=int, default=21, help="미래수익 horizon(거래일)")
    a = p.parse_args(argv)
    for mk in a.markets:
        df = rank_ic(mk, a.horizon)
        print(f"\n===== {mk}: fwd={a.horizon}d, 피처 {len(df)}개 (|meanIC| 내림차순) =====")
        print(f"{'feature':16}{'meanIC':>9}{'ICIR':>8}{'%+day':>8}{'NW_t':>8}{'days':>7}")
        print("-" * 56)
        for _, r in df.iterrows():
            flag = " *" if abs(r["nw_t"]) >= 3 else ("  ." if abs(r["nw_t"]) >= 2 else "")
            print(f"{r['feature']:16}{r['mean_ic']*100:>8.2f}%{r['icir']:>8.3f}"
                  f"{r['pct_pos']*100:>7.1f}%{r['nw_t']:>8.1f}{int(r['n_days']):>7}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
