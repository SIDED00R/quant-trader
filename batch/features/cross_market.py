"""크로스마켓 컨텍스트 피처 (단일 책임: KR 모델에 누설 없는 US 시장 데이터 부착).

미국장 lead-lag(US가 먼저 마감 → KR이 반응)를 피처로 쓴다. **타임존 누설 방지가 핵심**:
KR 거래일 D(09:00 KST=00:00 UTC 개장) 직전 종료된 미국장은 US 달력일 D-1(21:00 UTC 마감)이다.
US 달력일 D는 KR(D) 마감 뒤(22:30 KST~) 열리므로 **KR(D)는 US date < D 만 사용**(as-of backward,
allow_exact_matches=False). 같은 날짜 US(D)는 미래 → 금지.

주의: US 시장수준 집계(usx_mkt_*)는 한 날짜에 모든 KR 종목에 동일 → **횡단면 단변량 IC≈0**(정상).
효용은 트리/NN의 **상호작용(US 레짐 × 종목특성)**과 **종목별 US 민감도(usx_beta)**에서 나온다.
"""
import pandas as pd

EPS = 1e-12


def us_market_daily(us_panel: pd.DataFrame) -> pd.DataFrame:
    """US 유니버스 동일가중 일별 시장 집계 → [date, usx_mkt_ret, usx_breadth, usx_ret5, usx_ret21, usx_vol21]."""
    us = us_panel.sort_values(["symbol", "date"]).copy()
    us["ret"] = us.groupby("symbol")["close"].transform(lambda s: s / s.shift(1) - 1)
    agg = us.groupby("date").agg(usx_mkt_ret=("ret", "mean"),
                                 usx_breadth=("ret", lambda r: (r > 0).mean())).reset_index()
    agg = agg.sort_values("date").reset_index(drop=True)
    agg["usx_ret5"] = agg["usx_mkt_ret"].rolling(5, min_periods=3).sum()
    agg["usx_ret21"] = agg["usx_mkt_ret"].rolling(21, min_periods=10).sum()
    agg["usx_vol21"] = agg["usx_mkt_ret"].rolling(21, min_periods=10).std()
    return agg


def _asof_backward(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """left(KR, date별 다수 종목) ← right(US, date별 1행)을 date 기준 누설없이 결합.
    각 KR date D는 US date < D 중 최신만 사용(allow_exact_matches=False)."""
    l = left.sort_values("date").reset_index(drop=True)
    r = right.sort_values("date").reset_index(drop=True)
    return pd.merge_asof(l, r, on="date", direction="backward", allow_exact_matches=False)


def us_beta(kr_panel: pd.DataFrame, us_market: pd.DataFrame, win: int = 60) -> pd.DataFrame:
    """종목별 US 시장민감도(lead-lag 베타): KR 종목 일수익 vs 직전 US 시장수익의 롤링 베타.
    → [symbol, date, usx_beta, usx_corr]. 누설없음(US date < KR date)."""
    kr = kr_panel.sort_values(["symbol", "date"]).copy()
    kr["kr_ret"] = kr.groupby("symbol")["close"].transform(lambda s: s / s.shift(1) - 1)
    usm = us_market[["date", "usx_mkt_ret"]]
    kr = _asof_backward(kr, usm)                       # 각 KR(D)에 US(<D) 시장수익 부착

    def _b(g: pd.DataFrame) -> pd.DataFrame:
        g = g.sort_values("date")
        x, y = g["usx_mkt_ret"], g["kr_ret"]
        cov = y.rolling(win, min_periods=win // 2).cov(x)
        var = x.rolling(win, min_periods=win // 2).var()
        corr = y.rolling(win, min_periods=win // 2).corr(x)
        return pd.DataFrame({"symbol": g["symbol"].values, "date": g["date"].values,
                             "usx_beta": (cov / (var + EPS)).values, "usx_corr": corr.values})

    return kr.groupby("symbol", group_keys=False).apply(_b).reset_index(drop=True)


def attach_us_context(kr_feats: pd.DataFrame, kr_panel: pd.DataFrame, us_panel: pd.DataFrame) -> pd.DataFrame:
    """KR 피처 패널에 누설없는 US 컨텍스트(시장집계 usx_* + 종목별 usx_beta/corr) 결합."""
    usm = us_market_daily(us_panel)
    merged = _asof_backward(kr_feats, usm)             # 시장수준(누설없음)
    beta = us_beta(kr_panel, usm)                       # 종목수준(누설없음)
    return merged.merge(beta, on=["symbol", "date"], how="left")
