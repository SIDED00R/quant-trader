"""KR 미시구조 피처 파생 (단일 책임: KRX 수급·공매도·외국인보유 → 일별 피처, 누설없음).

저장소(stock_investor_flow·stock_foreign_holding·stock_short)에서 KR 전용 비-가격 신호를 만든다.
누설방지: 모든 KR 외부데이터는 거래일 EOD 이후 확정/지연발표 → 거래일 d 피처는 d 이전 자료만
사용(as-of backward, allow_exact_matches=False). 공매도 잔고는 T+2 발표라 1거래일 추가 시프트.
다중공선성 회피(doc): 수급은 raw 대신 거래대금 정규화, 보유율·공매도잔고는 수준+Δ.

batch.ml.dataset(KR 경로)에서 join. 테이블 부재(수집 전)면 빈 DF 반환(US 경로 무영향).
"""
import numpy as np
import pandas as pd

from common.clickhouse_client import create_client

EPS = 1e-12
_INST = ["fin_invest", "insurance", "invest_trust", "private_fund", "bank", "other_finance", "pension"]


def _query(ch, sql: str) -> pd.DataFrame:
    try:
        rows = ch.query(sql).result_rows
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _asof_excl(dates: pd.Series, series: pd.DataFrame, col: str) -> np.ndarray:
    """거래일별 date보다 '이전'(미포함)의 최신 col 값 — 발표지연 누설 차단."""
    if series.empty or col not in series:
        return np.full(len(dates), np.nan)
    left = pd.DataFrame({"date": pd.to_datetime(dates.values)}).sort_values("date")
    r = series[["date", col]].dropna(subset=["date"]).sort_values("date")
    m = pd.merge_asof(left, r, on="date", direction="backward", allow_exact_matches=False)
    return m.sort_index()[col].to_numpy()


def daily_kr_microstructure_from_store(panel: pd.DataFrame, log=print) -> pd.DataFrame:
    """panel[symbol,date,close,volume] → [symbol,date, kr_*] (누설없는 KR 미시구조 피처)."""
    ch = create_client()
    flow = _query(ch, "SELECT date, symbol, investor, net_value FROM stock_investor_flow FINAL")
    hold = _query(ch, "SELECT date, symbol, holding_ratio FROM stock_foreign_holding FINAL")
    short = _query(ch, "SELECT date, symbol, short_balance_ratio, short_volume_ratio FROM stock_short FINAL WHERE market = 'KR'")
    if flow.empty and hold.empty and short.empty:
        log("[kr-micro] KRX 테이블 비어있음 — batch.rawdata.krx 먼저 실행")
        return pd.DataFrame()

    flow_by = {}
    if not flow.empty:
        flow.columns = ["date", "symbol", "investor", "net_value"]
        piv = flow.pivot_table(index=["symbol", "date"], columns="investor",
                               values="net_value", aggfunc="sum").reset_index()
        piv["foreign_net"] = piv.get("foreign", 0.0).fillna(0) + piv.get("other_foreign", 0.0).fillna(0)
        # 기관 순매수: 벌크 소스의 'institution'(KRX 기관합계, 연기금 포함)이 있으면 그대로,
        # per-symbol 소스면 상세 기관분류 합. 두 소스 모두 연기금 포함 = 기관합계 동일 의미.
        # pension은 별도 피처로만 사용(institution에 합산하면 연기금 이중계산 → 금지).
        if "institution" in piv:
            piv["inst_net"] = piv["institution"].fillna(0)
        else:
            piv["inst_net"] = piv[[c for c in _INST if c in piv]].sum(axis=1)
        piv["pension_net"] = piv.get("pension", 0.0).fillna(0)
        piv["date"] = pd.to_datetime(piv["date"])
        flow_by = {s: g for s, g in piv.groupby("symbol")}

    hold_by = {}
    if not hold.empty:
        hold.columns = ["date", "symbol", "holding_ratio"]
        hold["date"] = pd.to_datetime(hold["date"])
        hold = hold.sort_values(["symbol", "date"])
        hold["kr_fh_ratio"] = hold["holding_ratio"]
        hold["kr_fh_chg20"] = hold.groupby("symbol")["holding_ratio"].transform(lambda s: s - s.shift(20))
        hold_by = {s: g for s, g in hold.groupby("symbol")}

    short_by = {}
    if not short.empty:
        short.columns = ["date", "symbol", "short_balance_ratio", "short_volume_ratio"]
        short["date"] = pd.to_datetime(short["date"])
        short = short.sort_values(["symbol", "date"])
        # 잔고 T+2 발표 → 1거래일 추가 시프트(이후 as-of 미포함과 합쳐 ≥2일 지연)
        short["kr_short_bal_ratio"] = short.groupby("symbol")["short_balance_ratio"].shift(1)
        short["kr_short_bal_chg20"] = short.groupby("symbol")["short_balance_ratio"].transform(
            lambda s: s - s.shift(20)).groupby(short["symbol"]).shift(1)
        short["kr_short_vol_ratio"] = short["short_volume_ratio"]
        short_by = {s: g for s, g in short.groupby("symbol")}

    out = []
    for s, g in panel.groupby("symbol"):
        g = g.sort_values("date")
        d = g["date"]
        f = pd.DataFrame({"symbol": s, "date": d.values})
        if s in flow_by:
            fl = flow_by[s].sort_values("date").copy()
            dv = pd.DataFrame({"date": pd.to_datetime(d.values),
                               "dvol": (g["close"] * g["volume"]).values})
            fl = fl.merge(dv, on="date", how="outer").sort_values("date")
            for src, nm in (("foreign_net", "kr_foreign_flow20"), ("inst_net", "kr_inst_flow20"),
                            ("pension_net", "kr_pension_flow20")):
                num = fl[src].rolling(20, min_periods=10).sum()
                den = fl["dvol"].rolling(20, min_periods=10).sum()
                fl[nm] = num / (den + EPS)
                f[nm] = _asof_excl(d, fl, nm)
        if s in hold_by:
            for nm in ("kr_fh_ratio", "kr_fh_chg20"):
                f[nm] = _asof_excl(d, hold_by[s], nm)
        if s in short_by:
            for nm in ("kr_short_bal_ratio", "kr_short_bal_chg20", "kr_short_vol_ratio"):
                f[nm] = _asof_excl(d, short_by[s], nm)
        out.append(f)
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    log(f"[kr-micro] {res['symbol'].nunique() if len(res) else 0}종목 미시구조 피처")
    return res
