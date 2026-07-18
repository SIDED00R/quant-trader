"""피처 계산·저장 (단일 책임: OHLCV 로드 → 피처 계산 → stock_features_daily 적재).

롱 포맷(symbol, date, market, feature, value)으로 피처별 insert(메모리 안전). 재실행 멱등.
학습/IC는 이 테이블을 (symbol,date) 피벗하거나 compute_features로 온더플라이 재계산해 사용.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.features.compute [US KR] [--no-store]
"""
import argparse
import sys

import pandas as pd

from batch.features.ohlcv import compute_features, feature_columns
from common.clickhouse_client import create_client

_COLS = ["open", "high", "low", "close", "volume"]


def load_ohlcv(market: str) -> pd.DataFrame:
    """market(KR|US)의 일봉 OHLCV 패널 로드(시간 오름차순)."""
    rows = create_client().query(
        f"SELECT symbol, window_start, open, high, low, close, volume "
        f"FROM stock_candles_1d FINAL WHERE market='{market}' ORDER BY symbol, window_start").result_rows
    df = pd.DataFrame(rows, columns=["symbol", "date", *_COLS])
    for c in _COLS:
        df[c] = df[c].astype(float)
    if df.empty:
        raise RuntimeError(f"stock_candles_1d(market={market}) 0행 — 백필 필요: "
                           "python -m batch.candles.backfill_stock_daily")
    return df


def store(market: str, feats: pd.DataFrame) -> int:
    """피처 패널을 롱 포맷으로 stock_features_daily에 피처별 적재(NaN 제외). 적재 행수."""
    client = create_client()
    dates = pd.to_datetime(feats["date"]).dt.date
    total = 0
    for ft in feature_columns(feats):
        sub = pd.DataFrame({"symbol": feats["symbol"], "date": dates,
                            "market": market, "feature": ft, "value": feats[ft]})
        sub = sub[sub["value"].notna()]
        if sub.empty:
            continue
        client.insert("stock_features_daily", sub.values.tolist(),
                      column_names=["symbol", "date", "market", "feature", "value"])
        total += len(sub)
    return total


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="OHLCV 피처 계산 → stock_features_daily 적재")
    p.add_argument("markets", nargs="*", default=["US", "KR"])
    p.add_argument("--no-store", action="store_true", help="계산만 하고 적재 생략(검증용)")
    a = p.parse_args(argv)
    for mk in a.markets:
        panel = load_ohlcv(mk)
        feats = compute_features(panel)
        nfeat = len(feature_columns(feats))
        print(f"[features] {mk}: {panel['symbol'].nunique()}종목 → {len(feats):,}행 × {nfeat}피처")
        if not a.no_store:
            n = store(mk, feats)
            print(f"[features] {mk}: {n:,}개 (symbol,date,feature) 값 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
