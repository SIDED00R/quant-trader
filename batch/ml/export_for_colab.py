"""Colab GPU DL 비교용 데이터 export (단일 책임: CH → 자체완결 parquet 2개).

us_ohlcv.parquet  = raw OHLCV(시퀀스 DL 입력 구성용)
us_tabular.parquet = 확정 피처셋 70개(OHLCV파생+펀더멘털+13F) + label + fwd_ret (GBDT/MLP 입력)
Colab 노트북은 이 둘만 업로드해 GBDT(기준)·GRU·MLP를 T4에서 비교(ClickHouse 불요).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.ml.export_for_colab [--out <dir>]
"""
import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()
from batch.features.compute import load_ohlcv
from batch.ml.dataset import build_dataset


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Colab DL 비교용 parquet export")
    p.add_argument("--out", default="colab_data")
    p.add_argument("--horizon", type=int, default=21)
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    ohlcv = load_ohlcv("US")[["symbol", "date", "open", "high", "low", "close", "volume"]]
    fo = os.path.join(a.out, "us_ohlcv.parquet")
    ohlcv.to_parquet(fo, index=False)
    print(f"[export] us_ohlcv: {len(ohlcv):,}행 → {fo} ({os.path.getsize(fo)//1024//1024}MB)")

    # 확정 피처셋(매크로 제외 = 챔피언 3.56% config)
    feats, cols = build_dataset("US", horizon=a.horizon, fundamentals=True, macro=False, inst13f=True)
    keep = ["symbol", "date", *cols, "label", "fwd_ret"]
    ft = os.path.join(a.out, "us_tabular.parquet")
    feats[keep].to_parquet(ft, index=False)
    print(f"[export] us_tabular: {len(feats):,}행 × {len(cols)}피처 → {ft} ({os.path.getsize(ft)//1024//1024}MB)")
    print(f"[export] 피처: {cols}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
