"""Colab GPU DL 비교 (자체완결: us_tabular/us_ohlcv parquet → GBDT vs MLP vs GRU).

Colab T4에서 `colab exec -f` 또는 `colab run`으로 실행. ClickHouse·repo 불요(parquet 2개만).
- GBDT(lambdarank): 확정 피처 70개(공학+펀더멘털+13F) — 로컬 챔피언(3.56%) 재현
- MLP(GPU): 같은 70 피처 tabular DL
- GRU(GPU): raw OHLCV 5채널×60일 시퀀스
동일 purged/embargo walk-forward CV + Rank IC/ICIR/NW-t + 롱숏 Sharpe.

데이터 경로: 인자 1 또는 /content (Colab 기본). 결과 /content/colab_results.json 저장.
"""
import functools
import json
import os
import sys

import numpy as np
import pandas as pd

print = functools.partial(print, flush=True)            # Colab 출력 즉시 가시화(블록버퍼링 방지)
DATA = os.getenv("DL_DATA") or (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/content")
LB, HZ, EPS = 60, 21, 1e-9
SEEDS = int(os.getenv("DL_SEEDS", "5"))                 # 무료 GPU 회수 대비 env로 스케일 축소 가능
FOLDS = int(os.getenv("DL_FOLDS", "6"))
EPOCHS = int(os.getenv("DL_EPOCHS", "40"))


# ---- 공통: purged walk-forward + Rank IC 평가 ----
def folds(dates, n=FOLDS, horizon=HZ, embargo=5, min_train=252):
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


def summarize(df, label):
    sub = df.dropna(subset=["pred", "fwd_ret"])
    ic = sub.groupby("date").apply(
        lambda g: g["pred"].rank().corr(g["fwd_ret"].rank()) if len(g) >= 30 else np.nan).dropna()
    a = ic.to_numpy()

    def spread(g):
        if len(g) < 30:
            return np.nan
        k = max(1, len(g) // 10); s = g.sort_values("pred")
        return s["fwd_ret"].iloc[-k:].mean() - s["fwd_ret"].iloc[:k].mean()
    sp = sub.groupby("date").apply(spread).dropna()
    sh = sp.mean() / (sp.std() + EPS) * (252 / HZ) ** .5 if len(sp) > 20 else np.nan
    return {"model": label, "mean_ic": float(a.mean()), "icir": float(a.mean() / a.std()),
            "nw_t": float(nw_t(a)), "ls_sharpe": float(sh), "days": int(len(a))}


# ---- GBDT ----
def run_gbdt(tab, cols):
    import lightgbm as lgb
    P = dict(n_estimators=400, learning_rate=0.02, num_leaves=31, min_child_samples=200,
             feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=1.0, verbose=-1, n_jobs=-1)
    oof = []
    for tr_d, te_d in folds(tab["date"].unique()):
        tr = tab[tab["date"].isin(set(tr_d))]; te = tab[tab["date"].isin(set(te_d))]
        pred = np.zeros(len(te))
        for s in range(SEEDS):
            t = tr.sort_values("date").dropna(subset=["fwd_ret"])
            y = t.groupby("date")["fwd_ret"].transform(
                lambda v: pd.qcut(v.rank(method="first"), min(8, max(2, len(v))), labels=False, duplicates="drop")).fillna(0).astype(int)
            grp = t.groupby("date").size().to_numpy()
            m = lgb.LGBMRanker(objective="lambdarank", label_gain=list(range(8)), random_state=s, **P)
            m.fit(t[cols], y, group=grp)
            pred += m.predict(te[cols])
        oof.append(pd.DataFrame({"date": te["date"].values, "pred": pred / SEEDS, "fwd_ret": te["fwd_ret"].values}))
    return summarize(pd.concat(oof), "GBDT")


# ---- torch 공통 ----
def _torch():
    import torch
    return torch, ("cuda" if torch.cuda.is_available() else "cpu")


def run_mlp(tab, cols):
    torch, dev = _torch()
    import torch.nn as nn
    X = tab[cols].fillna(0.0).to_numpy(np.float32)
    y = tab["label"].to_numpy(np.float32)
    oof = []
    for tr_d, te_d in folds(tab["date"].unique()):
        trm = tab["date"].isin(set(tr_d)).to_numpy(); tem = tab["date"].isin(set(te_d)).to_numpy()
        pred = np.zeros(int(tem.sum()))
        for s in range(SEEDS):
            torch.manual_seed(s)
            net = nn.Sequential(nn.Linear(len(cols), 128), nn.ReLU(), nn.Dropout(0.3),
                                nn.Linear(128, 32), nn.ReLU(), nn.Linear(32, 1)).to(dev)
            opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
            Xt = torch.tensor(X[trm]); yt = torch.tensor(y[trm])
            dl = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(Xt, yt), batch_size=4096, shuffle=True)
            for _ in range(EPOCHS):
                for xb, yb in dl:
                    opt.zero_grad(); loss = ((net(xb.to(dev)).squeeze(-1) - yb.to(dev)) ** 2).mean()
                    loss.backward(); opt.step()
            net.eval()
            with torch.no_grad():
                pred += net(torch.tensor(X[tem]).to(dev)).squeeze(-1).cpu().numpy()
        d = tab.loc[tem]
        oof.append(pd.DataFrame({"date": d["date"].values, "pred": pred / SEEDS, "fwd_ret": d["fwd_ret"].values}))
    return summarize(pd.concat(oof), "MLP")


def run_gru(ohlcv):
    torch, dev = _torch()
    import torch.nn as nn
    p = ohlcv.sort_values(["symbol", "date"]).reset_index(drop=True)
    p["fwd"] = p.groupby("symbol")["close"].transform(lambda s: s.shift(-HZ) / s - 1)
    p["label"] = p.groupby("date")["fwd"].transform(lambda s: (s - s.mean()) / (s.std() + EPS))
    syms = {s: i for i, s in enumerate(p["symbol"].unique())}
    chan = {syms[s]: g[["open", "high", "low", "close", "volume"]].to_numpy() for s, g in p.groupby("symbol")}
    pos = p.groupby("symbol").cumcount().to_numpy(); sid = p["symbol"].map(syms).to_numpy()
    valid = (pos >= LB - 1) & p["label"].notna().to_numpy()
    meta = p.loc[valid, ["date", "fwd", "label"]].reset_index(drop=True)
    idx = np.stack([sid[valid], pos[valid]], 1)
    X = np.empty((len(idx), LB, 5), np.float32)
    for s in np.unique(idx[:, 0]):
        sel = np.where(idx[:, 0] == s)[0]; arr = chan[s].astype(np.float32)
        w = np.stack([arr[t - LB + 1:t + 1] for t in idx[sel, 1]])
        w[:, :, :4] /= (w[:, -1, 3:4][:, :, None] + EPS); w[:, :, 4] /= (w[:, :, 4].mean(1)[:, None] + EPS)
        X[sel] = w
    Xt = torch.from_numpy(X); yt = torch.from_numpy(meta["label"].to_numpy(np.float32))

    class G(nn.Module):
        def __init__(s):
            super().__init__(); s.g = nn.GRU(5, 64, 2, batch_first=True, dropout=0.2)
            s.h = nn.Sequential(nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.2), nn.Linear(32, 1))

        def forward(s, x):
            _, h = s.g(x); return s.h(h[-1]).squeeze(-1)
    oof = []
    for tr_d, te_d in folds(meta["date"].unique()):
        trm = torch.from_numpy(meta["date"].isin(set(tr_d)).to_numpy()); tem = torch.from_numpy(meta["date"].isin(set(te_d)).to_numpy())
        pred = np.zeros(int(tem.sum()))
        for s in range(SEEDS):
            torch.manual_seed(s); net = G().to(dev); opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
            dl = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(Xt[trm], yt[trm]), batch_size=2048, shuffle=True)
            for _ in range(EPOCHS):
                for xb, yb in dl:
                    opt.zero_grad(); loss = ((net(xb.to(dev)) - yb.to(dev)) ** 2).mean(); loss.backward(); opt.step()
            net.eval()
            with torch.no_grad():
                Xte = Xt[tem]
                pred += np.concatenate([net(Xte[i:i + 8192].to(dev)).cpu().numpy() for i in range(0, len(Xte), 8192)])
        d = meta.loc[tem.numpy()]
        oof.append(pd.DataFrame({"date": d["date"].values, "pred": pred / SEEDS, "fwd_ret": d["fwd"].values}))
    return summarize(pd.concat(oof), "GRU")


if __name__ == "__main__":
    import torch
    print(f"[colab_train] dev={'cuda' if torch.cuda.is_available() else 'cpu'} "
          f"({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})")
    import glob
    sel = os.getenv("DL_MODELS", "GBDT,MLP,GRU").split(",")   # GRU는 GPU 필요 — CPU면 DL_MODELS=GBDT,MLP
    out = f"{DATA}/colab_results.json"
    print(f"[colab_train] 모델={sel} 스케일 SEEDS={SEEDS} FOLDS={FOLDS} EPOCHS={EPOCHS}")
    tab, cols, ohlcv = None, [], None
    if "GBDT" in sel or "MLP" in sel:                 # tabular는 트리/MLP만 필요
        parts = sorted(glob.glob(f"{DATA}/tab_*.parquet"))
        tab = (pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True) if parts
               else pd.read_parquet(f"{DATA}/us_tabular.parquet"))
        cols = [c for c in tab.columns if c not in ("symbol", "date", "label", "fwd_ret")]
        tab[cols] = tab[cols].astype("float32")      # float16(업로드용)→float32(학습)
        print(f"[colab_train] tabular {tab.shape} ({len(cols)}피처)")
    if "GRU" in sel:                                  # GRU는 raw OHLCV만 필요(19MB 한 파일)
        ohlcv = pd.read_parquet(f"{DATA}/us_ohlcv.parquet")
        print(f"[colab_train] ohlcv {ohlcv.shape}")
    res = []
    for name, fn in [("GBDT", lambda: run_gbdt(tab, cols)),     # 빠른 것부터 — 회수돼도 직전까지 보존
                     ("MLP", lambda: run_mlp(tab, cols)),
                     ("GRU", lambda: run_gru(ohlcv))]:
        if name not in sel:
            continue
        print(f"[colab_train] {name} 시작...")
        r = fn()
        res.append(r)
        json.dump(res, open(out, "w"), indent=2)                # 모델별 증분 저장
        print(f"[colab_train] {name} 완료: meanIC={r['mean_ic']*100:.2f}% NW_t={r['nw_t']:.1f} → {out}")
    print(f"\n{'model':8}{'meanIC':>9}{'ICIR':>8}{'NW_t':>7}{'LS_Sharpe':>11}{'days':>7}")
    print("-" * 50)
    for r in res:
        print(f"{r['model']:8}{r['mean_ic']*100:>8.2f}%{r['icir']:>8.3f}{r['nw_t']:>7.1f}{r['ls_sharpe']:>11.2f}{r['days']:>7}")
    print(f"\n[colab_train] 결과 저장 → {out}")
