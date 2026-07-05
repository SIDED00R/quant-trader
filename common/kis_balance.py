"""KIS 잔고 정규화 (단일 책임: KR/US 모의계좌 잔고를 대시보드용 통일 dict로).

kis_account의 원본 조회를 대시보드 친화 형태로 정규화한다(읽기 전용·주문 없음).
- KR: 예수금(원) + 보유[{symbol,name,qty,avg,cur,eval,pnl}]
- US: 예수금(달러, 해외 매수가능금액조회 TR) + 보유[...]
모의/실전은 KIS_MOCK으로 kis_account가 자동 토글한다.
"""
from common.constants import BROKER_TIMEOUT
from common.http_client import get_json
from common.kis_account import (
    _headers,
    _tr,
    fetch_domestic_balance,
    fetch_overseas_balance,
    split_account,
    to_float as _f,
)
from common.config import KIS_REST_BASE
from common.rate_limit import acquire

_US_EXCHANGES = ("NASD", "NYSE", "AMEX")


def kr_balance() -> dict:
    """국내(KR) 모의 잔고 정규화. cash=주문가능 예수금(원)."""
    pos, summ = fetch_domestic_balance()
    cash = _f(summ.get("prvs_rcdl_excc_amt") or summ.get("dnca_tot_amt"))
    positions = [
        {
            "symbol": p.get("pdno"), "name": p.get("prdt_name"),
            "qty": _f(p.get("hldg_qty")), "avg": _f(p.get("pchs_avg_pric")),
            "cur": _f(p.get("prpr")), "eval": _f(p.get("evlu_amt")),
            "pnl": _f(p.get("evlu_pfls_amt")),
        }
        for p in pos if _f(p.get("hldg_qty")) > 0
    ]
    return {"market": "KR", "currency": "KRW", "cash": cash, "positions": positions}


def us_buyable_usd() -> float:
    """해외주식 매수가능 외화금액(USD). 명목 종목/가로 조회해 주문가능 외화금액을 cash 근사로 사용."""
    cano, prdt = split_account()
    acquire("kis", "rest")
    body = get_json(
        f"{KIS_REST_BASE}/uapi/overseas-stock/v1/trading/inquire-psamount",
        {
            "CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": "NASD",
            "OVRS_ORD_UNPR": "1", "ITEM_CD": "AAPL",
        },
        headers=_headers(_tr("TTTS3007R")), timeout=BROKER_TIMEOUT,
    )
    if str(body.get("rt_cd")) != "0":
        return 0.0
    out = body.get("output") or {}
    return _f(out.get("ord_psbl_frcr_amt") or out.get("frcr_ord_psbl_amt1"))


def us_balance() -> dict:
    """미국(US) 모의 잔고 정규화(거래소 합산). cash=주문가능 외화금액(달러)."""
    positions = []
    for ex in _US_EXCHANGES:
        try:
            pos, _ = fetch_overseas_balance(ex, "USD")
        except Exception:
            continue
        for p in pos:
            if _f(p.get("ovrs_cblc_qty")) > 0:
                positions.append({
                    "symbol": p.get("ovrs_pdno"), "name": p.get("ovrs_item_name"),
                    "qty": _f(p.get("ovrs_cblc_qty")), "avg": _f(p.get("pchs_avg_pric")),
                    "cur": _f(p.get("now_pric2")), "eval": _f(p.get("ovrs_stck_evlu_amt")),
                    "pnl": _f(p.get("frcr_evlu_pfls_amt")),
                })
    return {"market": "US", "currency": "USD", "cash": us_buyable_usd(), "positions": positions}


def us_held_qty(symbol: str, exchange: str) -> float:
    """US 단일 종목 보유수량(단일 거래소 1콜 — 체결확인용, us_balance 전체 4콜 회피 #218 C1)."""
    try:
        pos, _ = fetch_overseas_balance(exchange, "USD")
    except Exception:
        return 0.0
    return next((_f(p.get("ovrs_cblc_qty")) for p in pos if p.get("ovrs_pdno") == symbol), 0.0)
