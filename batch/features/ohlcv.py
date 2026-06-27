"""OHLCV 파생 피처 계산 (단일 책임: 일봉 패널 → 피처 패널).

문헌(docs/ml_features_research.md)의 즉시 계산 가능(computable_now) 피처를 구현한다.
입력: 패널 DataFrame[symbol, date, open, high, low, close, volume] (정렬 불요, 내부 정렬).
출력: DataFrame[symbol, date, <피처...>] (raw 값 — 횡단면 rank/z는 transforms.py에서 별도).

모든 피처는 시점 t까지의 정보만 사용(look-ahead 없음). 미래수익(라벨)은 ic.py에서 생성.
"""
import numpy as np
import pandas as pd

EPS = 1e-12


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(n, min_periods=n // 2).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=n // 2).mean()
    rs = up / (dn + EPS)
    return (100 - 100 / (1 + rs) - 50) / 50          # [-1,1] 정규화


def _corwin_schultz(high: pd.Series, low: pd.Series, win: int = 21) -> pd.Series:
    lh = np.log(high / low) ** 2
    beta = (lh + lh.shift(1))
    hi2 = np.maximum(high, high.shift(1))
    lo2 = np.minimum(low, low.shift(1))
    gamma = np.log(hi2 / lo2) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return s.clip(lower=0).rolling(win, min_periods=win // 2).mean()


def _yang_zhang(o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series, n: int) -> pd.Series:
    cc = c.shift(1)
    o_ = np.log(o / cc)                                # overnight
    u, d, cl = np.log(h / o), np.log(l / o), np.log(c / o)
    rs = u * (u - cl) + d * (d - cl)                   # Rogers-Satchell 일별
    vo = o_.rolling(n, min_periods=n // 2).var()
    vc = cl.rolling(n, min_periods=n // 2).var()
    vrs = rs.rolling(n, min_periods=n // 2).mean()
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return np.sqrt((vo + k * vc + (1 - k) * vrs).clip(lower=0))


def _per_symbol(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("date")
    o, h, l, c, v = g["open"], g["high"], g["low"], g["close"], g["volume"]
    ret = c / c.shift(1) - 1
    dv = c * v                                          # 거래대금
    f = pd.DataFrame({"symbol": g["symbol"].values, "date": g["date"].values})

    # --- 모멘텀/추세 ---
    f["mom1m_rev"] = -(c / c.shift(21) - 1).values
    f["mom6m"] = (c.shift(21) / c.shift(126) - 1).values
    f["mom12m"] = (c.shift(21) / c.shift(252) - 1).values
    f["mom36m_rev"] = -(c.shift(273) / c.shift(756) - 1).values
    f["chmom"] = ((c.shift(21) / c.shift(126) - 1) - (c.shift(126) / c.shift(252) - 1)).values
    f["rev_1w"] = -(c / c.shift(5) - 1).values
    hmax = h.rolling(252, min_periods=120).max()
    lmin = l.rolling(252, min_periods=120).min()
    f["high52"] = (c / hmax).values
    f["pos252"] = ((c - lmin) / (hmax - lmin + EPS)).values
    f["overnight21"] = np.log(o / c.shift(1)).rolling(21, min_periods=10).mean().values
    f["intraday21"] = np.log(c / o).rolling(21, min_periods=10).mean().values
    # 정보이산성 FIP (형성기 63일 양/음일 비율 × 누적부호)
    pret = c.shift(1) / c.shift(63) - 1
    pos_frac = (ret > 0).rolling(63, min_periods=30).mean().shift(1)
    f["fip"] = (np.sign(pret) * ((1 - pos_frac) - pos_frac)).values

    # --- 변동성/레인지 ---
    f["retvol21"] = ret.rolling(21, min_periods=10).std().values
    f["retvol63"] = ret.rolling(63, min_periods=30).std().values
    f["parkinson21"] = (0.361 * np.log(h / l) ** 2).rolling(21, min_periods=10).mean().values
    f["garman_klass21"] = (0.5 * np.log(h / l) ** 2 - 0.386 * np.log(c / o) ** 2).rolling(21, min_periods=10).mean().values
    yz20, yz60 = _yang_zhang(o, h, l, c, 20), _yang_zhang(o, h, l, c, 60)
    f["yang_zhang20"] = yz20.values
    f["vol_termstruct"] = (yz20 / (yz60 + EPS)).values
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    f["atr14"] = (tr.ewm(alpha=1 / 14, min_periods=7).mean() / c).values
    f["volofvol"] = ret.rolling(21, min_periods=10).std().rolling(63, min_periods=30).std().values
    f["skew21"] = ret.rolling(21, min_periods=10).skew().values
    f["kurt21"] = ret.rolling(21, min_periods=10).kurt().values
    r2 = ret ** 2
    semi_up = (r2 * (ret > 0)).rolling(21, min_periods=10).sum()
    semi_dn = (r2 * (ret < 0)).rolling(21, min_periods=10).sum()
    f["signed_jump"] = ((semi_up - semi_dn) / (semi_up + semi_dn + EPS)).values
    f["maxret"] = ret.rolling(21, min_periods=10).max().values
    f["mom_volscaled"] = (f["mom12m"].values) / (f["retvol21"].values * np.sqrt(252) + EPS)

    # --- 유동성/거래량 ---
    f["amihud21"] = (ret.abs() / (dv + EPS)).rolling(21, min_periods=10).mean().values
    f["dolvol21"] = np.log(dv.rolling(21, min_periods=10).mean() + 1).values
    f["std_dolvol"] = np.log(dv + 1).rolling(21, min_periods=10).std().values
    f["vol_zscore"] = ((v - v.rolling(21, min_periods=10).mean()) / (v.rolling(21, min_periods=10).std() + EPS)).values
    f["corwin_schultz"] = _corwin_schultz(h, l).values
    dp = c.diff()
    cov = dp.rolling(21, min_periods=10).cov(dp.shift(1))
    f["roll_spread"] = (2 * np.sqrt((-cov).clip(lower=0)) / c).values
    f["signed_dolvol"] = ((np.sign(ret) * dv).rolling(21, min_periods=10).sum() / (dv.rolling(21, min_periods=10).sum() + EPS)).values

    # --- Alpha158 KBar (단일일) ---
    f["kmid"] = ((c - o) / (o + EPS)).values
    f["klen"] = ((h - l) / (o + EPS)).values
    f["kmid2"] = ((c - o) / (h - l + EPS)).values
    f["kup"] = ((h - np.maximum(o, c)) / (o + EPS)).values
    f["klow"] = ((np.minimum(o, c) - l) / (o + EPS)).values
    f["ksft"] = ((2 * c - h - l) / (o + EPS)).values

    # --- Alpha158 롤링(대표) ---
    for w in (10, 20, 60):
        f[f"ma{w}_ratio"] = (c.rolling(w, min_periods=w // 2).mean() / c).values
        f[f"std{w}"] = (c.rolling(w, min_periods=w // 2).std() / c).values
        rmin = l.rolling(w, min_periods=w // 2).min()
        rmax = h.rolling(w, min_periods=w // 2).max()
        f[f"rsv{w}"] = ((c - rmin) / (rmax - rmin + EPS)).values
        f[f"corr{w}"] = c.rolling(w, min_periods=w // 2).corr(np.log(v + 1)).values
        dc = c.diff()
        f[f"sump{w}"] = (dc.clip(lower=0).rolling(w, min_periods=w // 2).sum() /
                         (dc.abs().rolling(w, min_periods=w // 2).sum() + EPS)).values   # RSI류
        wv = (ret.abs() * v)
        f[f"wvma{w}"] = (wv.rolling(w, min_periods=w // 2).std() / (wv.rolling(w, min_periods=w // 2).mean() + EPS)).values

    # --- 기술지표(정규화) ---
    f["rsi14"] = _rsi(c).values
    ema12, ema26 = c.ewm(span=12, min_periods=6).mean(), c.ewm(span=26, min_periods=13).mean()
    macd = ema12 - ema26
    f["macd_hist"] = ((macd - macd.ewm(span=9, min_periods=5).mean()) / c).values
    ma20, sd20 = c.rolling(20, min_periods=10).mean(), c.rolling(20, min_periods=10).std()
    f["bb_pctb"] = ((c - (ma20 - 2 * sd20)) / (4 * sd20 + EPS)).values
    return f


def compute_features(panel: pd.DataFrame) -> pd.DataFrame:
    """패널 OHLCV → 피처 패널. 종목별 시계열로 계산(look-ahead 없음)."""
    out = panel.groupby("symbol", group_keys=False).apply(_per_symbol)
    return out.reset_index(drop=True)


def feature_columns(df: pd.DataFrame) -> list:
    return [c for c in df.columns if c not in ("symbol", "date")]
