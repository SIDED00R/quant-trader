"""GRU 시퀀스 DL 베이스라인 (단일 책임: raw OHLCV 시퀀스 → 횡단면 수익예측 GRU).

서베이상 시퀀스 DL은 트리(공학피처)와 직교적 정보원. 입력 = Alpha360식 5채널(O/H/L/C/V)×60일,
당일 종가·평균거래량으로 정규화(메모리 절약 위해 윈도 on-the-fly 슬라이싱). 라벨 = 일별 횡단면
z-score(GBDT와 동일). 동일 purged walk-forward CV + Rank IC 평가로 GBDT와 head-to-head.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.ml.dl_gru US [--folds 3 --seeds 2 --epochs 20]
"""
import argparse
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from dotenv import load_dotenv

load_dotenv()
from batch.features.compute import load_ohlcv
from batch.ml.cv import purged_walkforward
from batch.ml.evaluate import print_summary, summarize

LB = 60
EPS = 1e-9


class GRUNet(nn.Module):
    def __init__(self, n_ch=5, hidden=48, layers=1, dropout=0.0):
        super().__init__()
        self.gru = nn.GRU(n_ch, hidden, layers, batch_first=True,
                          dropout=dropout if layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Linear(hidden, hidden // 2), nn.ReLU(),
                                  nn.Dropout(0.2), nn.Linear(hidden // 2, 1))

    def forward(self, x):
        _, h = self.gru(x)
        return self.head(h[-1]).squeeze(-1)


def _build_X(chan: dict, idx: np.ndarray) -> np.ndarray:
    """모든 샘플의 정규화 시퀀스를 한 번에 precompute → [N,LB,5] float32 (종목별 벡터화)."""
    X = np.empty((len(idx), LB, 5), dtype=np.float32)
    for s in np.unique(idx[:, 0]):
        sel = np.where(idx[:, 0] == s)[0]
        arr = chan[s].astype(np.float32)
        wins = np.stack([arr[t - LB + 1:t + 1] for t in idx[sel, 1]])  # [m,LB,5]
        c = wins[:, -1, 3:4][:, :, None] + EPS                         # 당일종가
        vm = wins[:, :, 4].mean(axis=1)[:, None] + EPS                 # 윈도평균 거래량
        wins[:, :, :4] /= c
        wins[:, :, 4] /= vm
        X[sel] = wins
    return X


def _prepare(market: str, horizon: int):
    panel = load_ohlcv(market).sort_values(["symbol", "date"]).reset_index(drop=True)
    panel["fwd"] = panel.groupby("symbol")["close"].transform(lambda s: s.shift(-horizon) / s - 1)
    panel["label"] = panel.groupby("date")["fwd"].transform(lambda s: (s - s.mean()) / (s.std() + EPS))
    syms = {s: i for i, s in enumerate(panel["symbol"].unique())}
    chan = {syms[s]: g[["open", "high", "low", "close", "volume"]].to_numpy()
            for s, g in panel.groupby("symbol")}
    pos = panel.groupby("symbol").cumcount().to_numpy()                # 종목 내 위치 t
    sid = panel["symbol"].map(syms).to_numpy()
    valid = (pos >= LB - 1) & panel["label"].notna().to_numpy()
    meta = panel.loc[valid, ["date", "symbol", "fwd", "label"]].reset_index(drop=True)
    idx = np.stack([sid[valid], pos[valid]], axis=1)
    return chan, idx, meta


def _device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _train_predict(Xtr, ytr, Xte, epochs, seed):
    torch.manual_seed(seed)
    dev = _device()
    net = GRUNet().to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
    lossf = nn.MSELoss()
    dl = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(Xtr, ytr),
                                     batch_size=2048, shuffle=True, num_workers=0)
    for _ in range(epochs):
        net.train()
        for xb, yb in dl:
            opt.zero_grad()
            loss = lossf(net(xb.to(dev)), yb.to(dev))
            loss.backward()
            opt.step()
    net.eval()
    preds = []
    with torch.no_grad():
        for i in range(0, len(Xte), 8192):
            preds.append(net(Xte[i:i + 8192].to(dev)).cpu().numpy())
    return np.concatenate(preds)


def run(market: str, horizon: int, folds: int, seeds: int, epochs: int) -> dict:
    chan, idx, meta = _prepare(market, horizon)
    dates = meta["date"].unique()
    print(f"[GRU-{market}] {len(meta):,} 샘플, 날짜 {len(dates)} ({folds}fold, {seeds}시드, {epochs}ep, dev={_device()}) 시퀀스 텐서 생성...")
    X = torch.from_numpy(_build_X(chan, idx))                 # [N,LB,5] 한 번만 precompute
    y = torch.from_numpy(meta["label"].to_numpy(np.float32))
    print(f"[GRU-{market}] X={tuple(X.shape)} 준비 완료")
    oof = []
    for i, (tr_d, te_d) in enumerate(purged_walkforward(dates, n_splits=folds, horizon=horizon)):
        trm = torch.from_numpy(meta["date"].isin(set(tr_d)).to_numpy())
        tem = torch.from_numpy(meta["date"].isin(set(te_d)).to_numpy())
        if not trm.any() or not tem.any():
            continue
        Xtr, ytr, Xte = X[trm], y[trm], X[tem]
        pred = np.zeros(int(tem.sum()))
        for s in range(seeds):
            pred += _train_predict(Xtr, ytr, Xte, epochs, s)
        pred /= seeds
        sub = meta.loc[tem.numpy(), ["date", "symbol", "fwd"]].reset_index(drop=True)
        sub["pred"] = pred
        oof.append(sub.rename(columns={"fwd": "fwd_ret"}))
        print(f"  fold{i+1}: train {int(trm.sum()):,} → test {int(tem.sum()):,} ({te_d[0]}~{te_d[-1]})")
    return summarize(pd.concat(oof, ignore_index=True), horizon=horizon, label=f"GRU-{market}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="GRU 시퀀스 DL 횡단면 수익예측")
    p.add_argument("markets", nargs="*", default=["US"])
    p.add_argument("--horizon", type=int, default=21)
    p.add_argument("--folds", type=int, default=3)
    p.add_argument("--seeds", type=int, default=2)
    p.add_argument("--epochs", type=int, default=20)
    a = p.parse_args(argv)
    rows = [run(mk, a.horizon, a.folds, a.seeds, a.epochs) for mk in a.markets]
    print("\n===== GRU 시퀀스 DL (OOF, purged walk-forward) =====")
    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
