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

# 저SNR 강정규화 파라미터
_PARAMS = dict(objective="regression", n_estimators=400, learning_rate=0.02,
               num_leaves=31, min_child_samples=200, feature_fraction=0.7,
               bagging_fraction=0.7, bagging_freq=1, lambda_l2=1.0,
               max_depth=-1, verbose=-1, n_jobs=-1)


def _fit_predict(Xtr, ytr, Xte, seeds: int):
    """시드앙상블: seeds개 모델 예측 평균."""
    preds = np.zeros(len(Xte))
    for s in range(seeds):
        m = lgb.LGBMRegressor(random_state=s, **_PARAMS)
        m.fit(Xtr, ytr)
        preds += m.predict(Xte)
    return preds / seeds


def run_market(market: str, horizon: int, seeds: int, folds: int) -> dict:
    feats, cols = build_dataset(market, horizon)
    feats = feats.dropna(subset=["label"])               # 라벨 결측(말미 horizon) 제거
    dates = feats["date"].unique()
    print(f"[{market}] {feats['symbol'].nunique()}종목 {len(feats):,}행 {len(cols)}피처, "
          f"날짜 {len(dates)} ({horizon}d horizon, {seeds}시드, {folds}fold)")
    oof = []
    for i, (tr_dates, te_dates) in enumerate(purged_walkforward(dates, n_splits=folds, horizon=horizon)):
        tr = feats[feats["date"].isin(set(tr_dates))]
        te = feats[feats["date"].isin(set(te_dates))]
        if tr.empty or te.empty:
            continue
        pred = _fit_predict(tr[cols], tr["label"], te[cols], seeds)
        oof.append(pd.DataFrame({"date": te["date"].values, "symbol": te["symbol"].values,
                                 "pred": pred, "fwd_ret": te["fwd_ret"].values}))
        print(f"  fold{i+1}: train {len(tr):,} ({tr_dates[0]}~{tr_dates[-1]}) → test {len(te):,} ({te_dates[0]}~{te_dates[-1]})")
    oof = pd.concat(oof, ignore_index=True)
    return summarize(oof, horizon=horizon, label=f"LGBM-{market}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="LightGBM 횡단면 수익예측 베이스라인")
    p.add_argument("markets", nargs="*", default=["US", "KR"])
    p.add_argument("--horizon", type=int, default=21)
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--folds", type=int, default=8)
    a = p.parse_args(argv)
    rows = [run_market(mk, a.horizon, a.seeds, a.folds) for mk in a.markets]
    print(f"\n===== LightGBM 베이스라인 (OOF, purged walk-forward) =====")
    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
