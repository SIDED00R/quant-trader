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
        # 0.0 반환은 인증/TR 오류를 '잔고 없음'으로 위장(사이징 0 → 조용한 무매매) — 크게 실패한다.
        raise RuntimeError(f"KIS 매수가능조회 실패(TTTS3007R): {body.get('msg_cd')} {body.get('msg1')}")
    out = body.get("output") or {}
    return _f(out.get("ord_psbl_frcr_amt") or out.get("frcr_ord_psbl_amt1"))


def us_balance() -> dict:
    """미국(US) 모의 잔고 정규화(거래소 합산). cash=주문가능 외화금액(달러).

    거래소 1곳이라도 조회 실패하면 예외 전파(부분 잔고 금지) — 누락된 거래소의 보유분이
    build_plan에서 '미보유'로 보여 이미 든 종목을 재매수(이중 배분)하게 되기 때문.
    콜러(트레이드 잡·API 라우트)는 전부 catch→통보/재시도 경로를 갖는다.
    """
    positions = []
    for ex in _US_EXCHANGES:
        pos, _ = fetch_overseas_balance(ex, "USD")
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
    """US 단일 종목 보유수량(단일 거래소 1콜 — 체결확인용, us_balance 전체 4콜 회피 #218 C1).

    조회 실패는 예외 전파(0.0 위장 금지 — '미체결'로 오판돼 취소/재주문을 유발).
    체결확인 단계별 처리(주문 전=중단, 폴링 중=보수적 유지)는 콜러 kis_chase가 담당한다.
    """
    pos, _ = fetch_overseas_balance(exchange, "USD")
    return next((_f(p.get("ovrs_cblc_qty")) for p in pos if p.get("ovrs_pdno") == symbol), 0.0)
