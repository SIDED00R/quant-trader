"""US 펀더멘털/기관보유 피처 (단일 책임: 저장된 분기 데이터 → 일별 PIT 피처 파생, 누설없음).

배선된 진입점은 저장소 파생: fundamentals_quarterly·institutional_13f → 일별 피처
(daily_fundamentals_from_store·daily_13f_from_store, JSON 재파싱 없이 빠름).
원본 적재용 companyfacts 페치·태그 정의(fetch_companyfacts·_entries·_SHARES 등)는
batch.data.fundamentals 가 사용하고, ticker_cik_map은 batch.data.fundamentals·batch.data.sec_sector가
공유한다. JSON 캐시는 common.cache. SEC 예절: User-Agent + ≤10req/s.

**point-in-time**: 각 거래일 d는 filed_date ≤ d 인 공시값만 사용(as-of backward) → look-ahead 차단.
- instant 계정(상장주식수·자본·자산): 최신 filed≤d 값.
- flow 계정(순이익·매출): 단일분기 합으로 TTM 구성(최신분기 filed로 가용시점 결정), 분기별 YoY 성장.
파생: 시가총액(mktcap=close×shares, GKX 중요도 2위)·회전율(turnover)·PBR·PER·PSR·ROE·ROA·매출성장.
"""
import os
import time

import httpx
from common.cache import dump_json, load_json
from common.constants import SEC_USER_AGENT
import numpy as np
import pandas as pd

_UA = {"User-Agent": SEC_USER_AGENT}
_CACHE = os.path.join(os.path.dirname(__file__), ".edgar_cache")
EPS = 1e-12

# 계정 태그(우선순위 폴백)
_SHARES = ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
           "WeightedAverageNumberOfDilutedSharesOutstanding"]
_EQUITY = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
_ASSETS = ["Assets"]
_NI = ["NetIncomeLoss", "ProfitLoss"]
_REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]


def ticker_cik_map() -> dict:
    with httpx.Client(timeout=30, headers=_UA) as c:
        j = c.get("https://www.sec.gov/files/company_tickers.json").json()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in j.values()}


def fetch_companyfacts(cik: str, client: httpx.Client, req_sleep: float = 0.12) -> dict | None:
    os.makedirs(_CACHE, exist_ok=True)
    fp = os.path.join(_CACHE, f"{cik}.json")
    if os.path.exists(fp):
        try:
            return load_json(fp)
        except Exception:
            pass
    time.sleep(req_sleep)
    r = client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    if r.status_code != 200:
        return None
    j = r.json()
    dump_json(fp, j)
    return j


def _entries(facts: dict, names: list) -> list:
    """facts(us-gaap/dei)에서 첫 매칭 태그의 단위 레코드 리스트 반환."""
    for ns in ("us-gaap", "dei"):
        block = facts.get("facts", {}).get(ns, {})
        for nm in names:
            if nm in block:
                for unit in block[nm]["units"].values():
                    return unit
    return []


def _asof(dates: pd.Series, series: pd.DataFrame, col: str) -> np.ndarray:
    """각 거래일에 filed≤date 인 최신 col 값(누설없음)."""
    if series.empty:
        return np.full(len(dates), np.nan)
    left = pd.DataFrame({"date": pd.to_datetime(dates.values)}).sort_values("date")
    r = series.rename(columns={"filed": "date"})[["date", col]].sort_values("date")
    m = pd.merge_asof(left, r, on="date", direction="backward")
    return m.sort_index()[col].to_numpy()


def _features_from_concepts(sym: str, g: pd.DataFrame, cf: pd.DataFrame) -> pd.DataFrame:
    """저장된 분기 facts(cf: 한 종목)로 일별 펀더멘털 피처 파생(누설없음)."""
    def inst(concept):
        s = cf[(cf["concept"] == concept) & (cf["duration_d"] == 0)][["filed_date", "value"]]
        s = s.rename(columns={"filed_date": "filed", "value": "val"}).sort_values("filed").drop_duplicates("filed", keep="last")
        return _asof(g["date"], s, "val")

    def ttm(concept):
        q = cf[(cf["concept"] == concept) & (cf["duration_d"].between(80, 100))][["period_end", "filed_date", "value"]]
        if q.empty:
            return np.full(len(g), np.nan), np.full(len(g), np.nan)
        q = q.sort_values(["period_end", "filed_date"]).drop_duplicates("period_end", keep="last").reset_index(drop=True)
        q["ttm"] = q["value"].rolling(4).sum()
        q["yoy"] = q["ttm"] / q["ttm"].shift(4) - 1
        q = q.rename(columns={"filed_date": "filed"}).dropna(subset=["ttm"]).sort_values("filed")
        return _asof(g["date"], q[["filed", "ttm"]], "ttm"), _asof(g["date"], q[["filed", "yoy"]], "yoy")

    sh, eq, at = inst("shares"), inst("equity"), inst("assets")
    ni, _ = ttm("net_income"); rv, rv_yoy = ttm("revenue")
    close, vol = g["close"].to_numpy(), g["volume"].to_numpy()
    mktcap = close * sh
    f = pd.DataFrame({"symbol": sym, "date": g["date"].values})
    f["fund_mktcap"] = np.where(mktcap > 0, np.log(np.where(mktcap > 0, mktcap, 1)), np.nan)
    f["fund_turnover"] = vol / (sh + EPS)
    f["fund_pbr"] = mktcap / (eq + EPS)
    f["fund_per"] = mktcap / np.where(ni != 0, ni, np.nan)   # ni=0만 NaN(PER 정의불가)·음수(적자)는 보존
    f["fund_psr"] = mktcap / (rv + EPS)
    f["fund_roe"] = ni / (eq + EPS)
    f["fund_roa"] = ni / (at + EPS)
    f["fund_rev_growth"] = rv_yoy
    return f


def daily_fundamentals_from_store(panel: pd.DataFrame, log=print) -> pd.DataFrame:
    """저장된 fundamentals_quarterly에서 일별 펀더멘털 피처 파생(JSON 재파싱 없이 빠름)."""
    from common.clickhouse_client import create_client
    rows = create_client().query(
        "SELECT symbol, concept, period_end, filed_date, duration_d, value "
        "FROM fundamentals_quarterly FINAL").result_rows
    if not rows:
        log("[edgar] fundamentals_quarterly 비어있음 — batch.data.fundamentals 먼저 실행")
        return pd.DataFrame()
    cf = pd.DataFrame(rows, columns=["symbol", "concept", "period_end", "filed_date", "duration_d", "value"])
    cf["filed_date"] = pd.to_datetime(cf["filed_date"]); cf["period_end"] = pd.to_datetime(cf["period_end"])
    cf["value"] = cf["value"].astype(float)
    by_sym = {s: gg for s, gg in cf.groupby("symbol")}
    out = [_features_from_concepts(s, g.sort_values("date"), by_sym[s])
           for s, g in panel.groupby("symbol") if s in by_sym]
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    log(f"[edgar] {res['symbol'].nunique() if len(res) else 0}종목 펀더멘털(저장소)")
    return res


def daily_13f_from_store(panel: pd.DataFrame, log=print) -> pd.DataFrame:
    """저장된 institutional_13f에서 일별 기관보유 피처 파생(누설없음).

    13F 마감은 분기말+45일 → filed=period_end+45d as-of(그 이후만 사용). 보유기관수·총주식수(로그)
    + QoQ 변화(기관 누적/분산 신호). df[symbol,date,f13_*].
    """
    from common.clickhouse_client import create_client
    rows = create_client().query(
        "SELECT symbol, period_end, num_holders, total_shares FROM institutional_13f FINAL").result_rows
    if not rows:
        log("[13f] institutional_13f 비어있음")
        return pd.DataFrame()
    q = pd.DataFrame(rows, columns=["symbol", "period_end", "num_holders", "total_shares"])
    q["period_end"] = pd.to_datetime(q["period_end"])
    q["filed"] = q["period_end"] + pd.Timedelta(days=45)
    q = q.sort_values(["symbol", "period_end"])
    q["holders_qoq"] = q.groupby("symbol")["num_holders"].pct_change()
    q["shares_qoq"] = q.groupby("symbol")["total_shares"].pct_change()
    by = {s: gg for s, gg in q.groupby("symbol")}
    out = []
    for s, g in panel.groupby("symbol"):
        if s not in by:
            continue
        g = g.sort_values("date"); qs = by[s]
        f = pd.DataFrame({"symbol": s, "date": g["date"].values})
        f["f13_holders"] = np.log1p(_asof(g["date"], qs.rename(columns={"num_holders": "v"})[["filed", "v"]], "v"))
        f["f13_shares"] = np.log1p(_asof(g["date"], qs.rename(columns={"total_shares": "v"})[["filed", "v"]], "v"))
        f["f13_holders_qoq"] = _asof(g["date"], qs.rename(columns={"holders_qoq": "v"})[["filed", "v"]], "v")
        f["f13_shares_qoq"] = _asof(g["date"], qs.rename(columns={"shares_qoq": "v"})[["filed", "v"]], "v")
        out.append(f)
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    log(f"[13f] {res['symbol'].nunique() if len(res) else 0}종목 기관보유 피처")
    return res
