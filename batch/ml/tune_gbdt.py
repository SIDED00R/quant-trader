"""GBDT 누설없는 nested 하이퍼파라미터 튜닝 (단일 책임).

시간분리로 누설 차단: 앞 TUNE_FRAC 날짜=튜닝창(grid 탐색만), 나머지=평가창(탐색 미관여).
평가창에서 default vs tuned(탐색 best)를 동일 조건 비교 → 튜닝의 순(out-of-sample) 이득 측정.
다중검정 정직성: 탐색한 조합 수(grid_size)를 결과에 기록(DSR n_trials 반영용).

입력: {DATA}/us_tabular.parquet (DATA = DL_DATA env 또는 argv1, 기본 colab_data).
⚠ 입력 parquet 생성기(batch/ml/export_for_colab.py)는 #196에서 삭제 — 재생성하려면
`git show 8b51e53^:batch/ml/export_for_colab.py`로 복원해 1회 실행(colab_data엔 과거 스냅샷만 남음).
실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.ml.tune_gbdt [DATA]
"""
import functools
import json
import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

print = functools.partial(print, flush=True)
DATA = os.getenv("DL_DATA") or (sys.argv[1] if len(sys.argv) > 1 else "colab_data")
HZ, EPS, TUNE_FRAC = 21, 1e-9, 0.45


def purged_folds(dates, n, horizon=HZ, embargo=5, min_train=252):
    dates = np.array(sorted(set(dates)))
    for blk in np.array_split(dates[min_train:], n):
        if len(blk) == 0:
            continue
        cut = np.searchsorted(dates, blk[0]) - (horizon + embargo)
        if cut < min_train // 2:
            continue
        yield dates[:cut], blk


def nw_t(x, lag=HZ - 1):
    x = x[~np.isnan(x)]
    if len(x) < 20:
        return np.nan
    d = x - x.mean(); n = len(x); s = (d @ d) / n
    for L in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - L / (lag + 1)) * (d[L:] @ d[:-L]) / n
    se = (s / n) ** .5
    return x.mean() / se if se > 0 else np.nan


def rank_ic(df):
    ic = df.dropna(subset=["pred", "fwd_ret"]).groupby("date").apply(
        lambda g: g["pred"].rank().corr(g["fwd_ret"].rank()) if len(g) >= 30 else np.nan).dropna()
    return ic.to_numpy()


def ls_sharpe(df):
    sub = df.dropna(subset=["pred", "fwd_ret"])

    def spread(g):
        if len(g) < 30:
            return np.nan
        k = max(1, len(g) // 10); s = g.sort_values("pred")
        return s["fwd_ret"].iloc[-k:].mean() - s["fwd_ret"].iloc[:k].mean()
    sp = sub.groupby("date").apply(spread).dropna()
    return float(sp.mean() / (sp.std() + EPS) * (252 / HZ) ** .5) if len(sp) > 20 else np.nan


def _fit_predict(tr, te, cols, P, seeds):
    pred = np.zeros(len(te))
    for s in range(seeds):
        t = tr.sort_values("date")
        y = t.groupby("date")["fwd_ret"].transform(
            lambda v: pd.qcut(v.rank(method="first"), min(8, max(2, len(v))), labels=False, duplicates="drop")
        ).fillna(0).astype(int)
        grp = t.groupby("date").size().to_numpy()
        m = lgb.LGBMRanker(objective="lambdarank", label_gain=list(range(8)),
                           random_state=s, n_jobs=-1, verbose=-1, **P)
        m.fit(t[cols], y, group=grp)
        pred += m.predict(te[cols])
    return pred / seeds


def evaluate(data, cols, P, n_folds, seeds):
    oof = []
    for tr_d, te_d in purged_folds(data["date"].unique(), n_folds):
        tr = data[data["date"].isin(set(tr_d))]; te = data[data["date"].isin(set(te_d))]
        oof.append(pd.DataFrame({"date": te["date"].values,
                                 "pred": _fit_predict(tr, te, cols, P, seeds),
                                 "fwd_ret": te["fwd_ret"].values}))
    df = pd.concat(oof); a = rank_ic(df)
    return {"mean_ic": float(a.mean()), "icir": float(a.mean() / a.std()),
            "nw_t": float(nw_t(a)), "ls_sharpe": ls_sharpe(df), "days": int(len(a))}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tab = pd.read_parquet(f"{DATA}/us_tabular.parquet")
    cols = [c for c in tab.columns if c not in ("symbol", "date", "label", "fwd_ret")]
    tab[cols] = tab[cols].astype("float32")
    tab = tab.dropna(subset=["fwd_ret"]).sort_values("date").reset_index(drop=True)

    dts = np.array(sorted(tab["date"].unique()))
    split = dts[int(len(dts) * TUNE_FRAC)]
    tune = tab[tab["date"] < split]; ev = tab[tab["date"] >= split]
    print(f"tune days={tune['date'].nunique()} eval days={ev['date'].nunique()} split={split}")

    default = dict(n_estimators=400, learning_rate=0.02, num_leaves=31, min_child_samples=200,
                   feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=1.0)
    grid = []
    for nl in (15, 31, 63):
        for lr in (0.02, 0.05):
            for mc in (100, 300):
                for ff in (0.6, 0.8):
                    P = dict(default); P.update(num_leaves=nl, learning_rate=lr, min_child_samples=mc, feature_fraction=ff)
                    grid.append(P)
    print(f"grid size={len(grid)} (탐색은 튜닝창에서만 seeds=1·3fold)")

    scored = []
    for i, P in enumerate(grid):
        r = evaluate(tune, cols, P, n_folds=3, seeds=1)
        scored.append((r["mean_ic"], P))
        print(f"[{i+1}/{len(grid)}] tuneIC={r['mean_ic']*100:.2f}% nl={P['num_leaves']} lr={P['learning_rate']} mc={P['min_child_samples']} ff={P['feature_fraction']}")
    scored.sort(key=lambda x: -x[0])
    best = scored[0][1]
    print(f"BEST(tune)={best} IC={scored[0][0]*100:.2f}%")

    ev_def = evaluate(ev, cols, default, 4, seeds=3)
    ev_best = evaluate(ev, cols, best, 4, seeds=3)
    print("=== EVAL 창(held-out) default vs tuned ===")
    print("default:", ev_def)
    print("tuned  :", ev_best)
    json.dump({"grid_size": len(grid), "best_params": best,
               "eval_default": ev_def, "eval_tuned": ev_best,
               "tune_top5": [(ic, P) for ic, P in scored[:5]]},
              open(f"{DATA}/tune_gbdt_result.json", "w"), indent=2, default=str)
    print(f"저장 → {DATA}/tune_gbdt_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
