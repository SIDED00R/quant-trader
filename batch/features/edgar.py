"""US 펀더멘털 수집 (단일 책임: SEC EDGAR companyfacts → 일별 펀더멘털 피처, 누설없음).

키리스. **point-in-time**: 각 거래일 d는 filed_date ≤ d 인 공시값만 사용(as-of backward) → look-ahead 차단.
- instant 계정(상장주식수·자본·자산): 최신 filed≤d 값.
- flow 계정(순이익·매출): 단일분기 합으로 TTM 구성(최신분기 filed로 가용시점 결정), 분기별 YoY 성장.
파생: 시가총액(mktcap=close×shares, GKX 중요도 2위)·회전율(turnover)·PBR·PER·PSR·ROE·ROA·매출성장.
companyfacts JSON은 캐시(재호출 회피). SEC 예절: User-Agent + ≤10req/s.
"""
import json
import os
import time

import httpx
import numpy as np
import pandas as pd

_UA = {"User-Agent": "coin-auto-trader research jh.lee@kornukopia-ai.com"}
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
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            pass
    time.sleep(req_sleep)
    r = client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    if r.status_code != 200:
        return None
    j = r.json()
    json.dump(j, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
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


def _instant(facts: dict, names: list) -> pd.DataFrame:
    """instant 계정 → df[filed, val] (filed 오름차순, 동일 filed 최신값)."""
    rows = [(e["filed"], e["val"]) for e in _entries(facts, names) if "filed" in e and e.get("val") is not None]
    if not rows:
        return pd.DataFrame(columns=["filed", "val"])
    df = pd.DataFrame(rows, columns=["filed", "val"])
    df["filed"] = pd.to_datetime(df["filed"])
    return df.sort_values("filed").drop_duplicates("filed", keep="last")


def _ttm(facts: dict, names: list) -> pd.DataFrame:
    """flow 계정 → 단일분기 추출 후 TTM(rolling 4) df[filed, ttm, yoy]. filed=TTM 완성 공시일."""
    es = [e for e in _entries(facts, names)
          if e.get("start") and e.get("end") and e.get("val") is not None and "filed" in e]
    q = []
    for e in es:
        d = (pd.to_datetime(e["end"]) - pd.to_datetime(e["start"])).days
        if 80 <= d <= 100:                          # 단일분기만
            q.append((e["end"], e["filed"], e["val"]))
    if not q:
        return pd.DataFrame(columns=["filed", "ttm", "yoy"])
    df = pd.DataFrame(q, columns=["end", "filed", "val"])
    df["end"] = pd.to_datetime(df["end"]); df["filed"] = pd.to_datetime(df["filed"])
    df = df.sort_values(["end", "filed"]).drop_duplicates("end", keep="last").reset_index(drop=True)
    df["ttm"] = df["val"].rolling(4).sum()
    df["yoy"] = df["ttm"] / df["ttm"].shift(4) - 1
    return df[["filed", "ttm", "yoy"]].dropna(subset=["ttm"]).sort_values("filed")


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
    f["fund_per"] = mktcap / ni
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


def build_us_fundamentals(panel: pd.DataFrame, log=print) -> pd.DataFrame:
    """US 패널[symbol,date,close,volume] → 일별 펀더멘털 피처 df[symbol,date,fund_*,sector] (누설없음)."""
    cikmap = ticker_cik_map()
    out = []
    syms = sorted(panel["symbol"].unique())
    with httpx.Client(timeout=30, headers=_UA) as client:
        for i, sym in enumerate(syms):
            cik = cikmap.get(sym.upper())
            if not cik:
                continue
            try:
                facts = fetch_companyfacts(cik, client)
            except Exception:
                facts = None
            if not facts:
                continue
            g = panel[panel["symbol"] == sym].sort_values("date")
            d = g["date"]
            sh = _asof(d, _instant(facts, _SHARES), "val")
            eq = _asof(d, _instant(facts, _EQUITY), "val")
            at = _asof(d, _instant(facts, _ASSETS), "val")
            ni = _ttm(facts, _NI); rv = _ttm(facts, _REV)
            ni_ttm = _asof(d, ni.rename(columns={"ttm": "v"}), "v")
            rv_ttm = _asof(d, rv.rename(columns={"ttm": "v"}), "v")
            rv_yoy = _asof(d, rv.rename(columns={"yoy": "v"}), "v")
            close, vol = g["close"].to_numpy(), g["volume"].to_numpy()
            mktcap = close * sh
            f = pd.DataFrame({"symbol": sym, "date": g["date"].values})
            f["fund_mktcap"] = np.log(mktcap)                       # 규모(GKX #2)
            f["fund_turnover"] = vol / (sh + EPS)
            f["fund_pbr"] = mktcap / (eq + EPS)
            f["fund_per"] = mktcap / ni_ttm                         # 음수 EPS는 NaN/음수 → 모델이 처리
            f["fund_psr"] = mktcap / (rv_ttm + EPS)
            f["fund_roe"] = ni_ttm / (eq + EPS)
            f["fund_roa"] = ni_ttm / (at + EPS)
            f["fund_rev_growth"] = rv_yoy
            out.append(f)
            if (i + 1) % 100 == 0:
                log(f"[edgar] {i+1}/{len(syms)}")
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    log(f"[edgar] {res['symbol'].nunique() if len(res) else 0}종목 펀더멘털 생성")
    return res
