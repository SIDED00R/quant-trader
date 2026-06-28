"""학습 데이터 조립 (단일 책임: OHLCV → 피처 + 라벨 행렬, 시장별).

- 피처: batch/features/ohlcv(58) + (KR) 누설없는 US 컨텍스트(cross_market).
- 라벨: 미래 horizon일 수익률(fwd_ret)을 **일별 횡단면 z-score**로(절대수익 아님 → 비정상 완화).
- 피처 정규화: 종목별 피처는 **일별 횡단면 rank[-1,1]**(GKX 표준, 레짐 상대화). 시장수준 US 피처
  (usx_mkt_* — 한 날짜 전종목 동일)는 rank 시 0이 되므로 raw 유지(트리 레짐 상호작용용).
키 정렬은 항상 (symbol,date) merge — 위치정렬 금지(누설/오정렬 방지).
"""
import numpy as np
import pandas as pd

from batch.features.compute import load_ohlcv
from batch.features.cross_market import attach_us_context
from batch.features.edgar import build_us_fundamentals
from batch.features.ohlcv import compute_features, feature_columns

EPS = 1e-12
# 시장수준(날짜별 상수) US 피처 — 횡단면 rank 제외, raw 유지
_MARKET_LEVEL = {"usx_mkt_ret", "usx_breadth", "usx_ret5", "usx_ret21", "usx_vol21"}


def _xs_rank(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """종목별 피처를 일별 횡단면 rank[-1,1]로 정규화(NaN 보존)."""
    g = df.groupby("date")
    for c in cols:
        df[c] = g[c].transform(lambda s: 2 * s.rank(pct=True) - 1)
    return df


def build_dataset(market: str, horizon: int = 21, rank_features: bool = True,
                  fundamentals: bool = True):
    """(feats, feature_cols) 반환. feats: [symbol,date,<피처>,fwd_ret,label].

    US: SEC EDGAR 펀더멘털(누설없음) 결합. KR: 누설없는 US 컨텍스트(수급/펀더멘털은 KRX/DART 키 후).
    """
    panel = load_ohlcv(market)
    feats = compute_features(panel)
    if market == "KR":
        feats = attach_us_context(feats, panel, load_ohlcv("US"))
    elif market == "US" and fundamentals:
        fund = build_us_fundamentals(panel[["symbol", "date", "close", "volume"]], log=lambda *a: None)
        if len(fund):
            feats = feats.merge(fund, on=["symbol", "date"], how="left")
    cols = feature_columns(feats)

    # 미래수익(라벨 원천) — 키 merge로 정렬
    fr = panel.sort_values(["symbol", "date"]).copy()
    fr["fwd_ret"] = fr.groupby("symbol")["close"].transform(lambda s: s.shift(-horizon) / s - 1)
    feats = feats.merge(fr[["symbol", "date", "fwd_ret"]], on=["symbol", "date"], how="left")

    if rank_features:
        feats = _xs_rank(feats, [c for c in cols if c not in _MARKET_LEVEL])

    # 라벨 = 일별 횡단면 z-score(fwd_ret)
    feats["label"] = feats.groupby("date")["fwd_ret"].transform(lambda s: (s - s.mean()) / (s.std() + EPS))
    return feats, cols
