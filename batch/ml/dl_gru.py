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
    def __init__(self, n_ch=5, hidden=64, layers=2, dropout=0.2):
        super().__init__()
        self.gru = nn.GRU(n_ch, hidden, layers, batch_first=True, dropout=dropout)
        self.head = nn.Sequential(nn.Linear(hidden, hidden // 2), nn.ReLU(), nn.Linear(hidden // 2, 1))

    def forward(self, x):
        _, h = self.gru(x)
        return self.head(h[-1]).squeeze(-1)


class _DS(torch.utils.data.Dataset):
    """on-the-fly 윈도: (sym_id,t) → 정규화된 [LB,5] + 라벨."""
    def __init__(self, chan, idx, y):
        self.chan, self.idx, self.y = chan, idx, y

    def __len__(self):
        return len(self.idx)

    def __getitem__(self, i):
        s, t = self.idx[i]
        w = self.chan[s][t - LB + 1:t + 1].astype(np.float32).copy()   # [LB,5] = O,H,L,C,V
        w[:, :4] /= (w[-1, 3] + EPS)                                    # OHLC / 당일종가
        w[:, 4] /= (w[:, 4].mean() + EPS)                              # V / 윈도평균
        return torch.from_numpy(w), np.float32(self.y[i])


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


def _train_predict(chan, tr_idx, tr_y, te_idx, epochs, seed):
    torch.manual_seed(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    net = GRUNet().to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
    lossf = nn.MSELoss()
    dl = torch.utils.data.DataLoader(_DS(chan, tr_idx, tr_y), batch_size=2048, shuffle=True, num_workers=0)
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
        te_dl = torch.utils.data.DataLoader(_DS(chan, te_idx, np.zeros(len(te_idx))), batch_size=4096)
        for xb, _ in te_dl:
            preds.append(net(xb.to(dev)).cpu().numpy())
    return np.concatenate(preds)


def run(market: str, horizon: int, folds: int, seeds: int, epochs: int) -> dict:
    chan, idx, meta = _prepare(market, horizon)
    dates = meta["date"].unique()
    print(f"[GRU-{market}] {len(meta):,} 샘플, 날짜 {len(dates)} ({folds}fold, {seeds}시드, {epochs}ep, dev={'cuda' if torch.cuda.is_available() else 'cpu'})")
    oof = []
    for i, (tr_d, te_d) in enumerate(purged_walkforward(dates, n_splits=folds, horizon=horizon)):
        trm = meta["date"].isin(set(tr_d)).to_numpy(); tem = meta["date"].isin(set(te_d)).to_numpy()
        if not trm.any() or not tem.any():
            continue
        tr_idx, tr_y = idx[trm], meta["label"].to_numpy()[trm]
        te_idx = idx[tem]
        pred = np.zeros(len(te_idx))
        for s in range(seeds):
            pred += _train_predict(chan, tr_idx, tr_y, te_idx, epochs, s)
        pred /= seeds
        sub = meta.loc[tem, ["date", "symbol", "fwd"]].reset_index(drop=True)
        sub["pred"] = pred
        oof.append(sub.rename(columns={"fwd": "fwd_ret"}))
        print(f"  fold{i+1}: train {trm.sum():,} → test {tem.sum():,} ({te_d[0]}~{te_d[-1]})")
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
