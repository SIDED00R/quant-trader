"""LightGBM 베이스라인 (단일 책임: 시장별 purged walk-forward + 시드앙상블 → OOF 평가).

모든 후속 모델(GRU/MASTER/HIST/TabPFN)의 **must-beat 게이트**. 회귀(라벨=횡단면 z-score) 후
예측을 횡단면 Rank IC로 평가. 저SNR 과적합 통제: 강정규화 + 시드앙상블(예측 평균).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.ml.baseline_lgbm [US KR] [--horizon 21] [--seeds 5] [--folds 8]
"""
import argparse
import sys

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
import lightgbm as lgb

from batch.ml.cv import purged_walkforward
from batch.ml.dataset import build_dataset
from batch.ml.evaluate import print_summary, summarize

# 저SNR 강정규화 파라미터(목적함수 무관 공통)
_PARAMS = dict(n_estimators=400, learning_rate=0.02, num_leaves=31,
               min_child_samples=200, feature_fraction=0.7, bagging_fraction=0.7,
               bagging_freq=1, lambda_l2=1.0, max_depth=-1, verbose=-1, n_jobs=-1)
_BUCKETS = 8   # lambdarank 라벨 등급 수


def _rank_labels(tr: pd.DataFrame) -> pd.Series:
    """일별 횡단면 fwd_ret을 0..B-1 정수 등급으로(lambdarank 라벨, 높을수록 우수)."""
    def b(s):
        nb = min(_BUCKETS, max(2, len(s)))
        try:
            return pd.qcut(s.rank(method="first"), nb, labels=False, duplicates="drop")
        except Exception:
            return pd.Series(0, index=s.index)
    return tr.groupby("date")["fwd_ret"].transform(b)


def _fit_predict(tr: pd.DataFrame, te: pd.DataFrame, cols: list, seeds: int, objective: str):
    """시드앙상블 예측 평균 + 평균 피처중요도. objective: regression | lambdarank."""
    preds = np.zeros(len(te))
    imp = np.zeros(len(cols))
    for s in range(seeds):
        if objective == "lambdarank":
            t = tr.sort_values("date").dropna(subset=["fwd_ret"]).copy()
            y = _rank_labels(t)
            t = t[y.notna()]; y = y[y.notna()].astype(int)
            grp = t.groupby("date").size().to_numpy()
            m = lgb.LGBMRanker(objective="lambdarank", label_gain=list(range(_BUCKETS)),
                               random_state=s, **_PARAMS)
            m.fit(t[cols], y, group=grp)
        else:
            m = lgb.LGBMRegressor(objective="regression", random_state=s, **_PARAMS)
            m.fit(tr[cols], tr["label"])
        preds += m.predict(te[cols])
        imp += m.feature_importances_
    return preds / seeds, imp / seeds


def run_market(market: str, horizon: int, seeds: int, folds: int, objective: str,
               fundamentals: bool = True, macro: bool = True) -> dict:
    feats, cols = build_dataset(market, horizon, fundamentals=fundamentals, macro=macro)
    feats = feats.dropna(subset=["label"])               # 라벨 결측(말미 horizon) 제거
    dates = feats["date"].unique()
    print(f"[{market}] {feats['symbol'].nunique()}종목 {len(feats):,}행 {len(cols)}피처, "
          f"날짜 {len(dates)} ({horizon}d, {seeds}시드, {folds}fold, obj={objective})")
    oof, imp_tot = [], np.zeros(len(cols))
    for i, (tr_dates, te_dates) in enumerate(purged_walkforward(dates, n_splits=folds, horizon=horizon)):
        tr = feats[feats["date"].isin(set(tr_dates))]
        te = feats[feats["date"].isin(set(te_dates))]
        if tr.empty or te.empty:
            continue
        pred, imp = _fit_predict(tr, te, cols, seeds, objective)
        imp_tot += imp
        oof.append(pd.DataFrame({"date": te["date"].values, "symbol": te["symbol"].values,
                                 "pred": pred, "fwd_ret": te["fwd_ret"].values}))
        print(f"  fold{i+1}: train {len(tr):,} → test {len(te):,} ({te_dates[0]}~{te_dates[-1]})")
    top = sorted(zip(cols, imp_tot), key=lambda x: -x[1])[:12]
    print(f"  [중요도 top12] {', '.join(f'{c}={int(v)}' for c, v in top)}")
    oof = pd.concat(oof, ignore_index=True)
    return summarize(oof, horizon=horizon, label=f"LGBM-{market}-{objective[:3]}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="LightGBM 횡단면 수익예측 베이스라인")
    p.add_argument("markets", nargs="*", default=["US", "KR"])
    p.add_argument("--horizon", type=int, default=21)
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--folds", type=int, default=6)
    p.add_argument("--objective", default="lambdarank", choices=["regression", "lambdarank"])
    p.add_argument("--no-fund", action="store_true", help="US 펀더멘털 제외")
    p.add_argument("--no-macro", action="store_true", help="매크로 제외")
    a = p.parse_args(argv)
    rows = [run_market(mk, a.horizon, a.seeds, a.folds, a.objective,
                       fundamentals=not a.no_fund, macro=not a.no_macro) for mk in a.markets]
    print(f"\n===== LightGBM 베이스라인 (OOF, purged walk-forward) =====")
    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
