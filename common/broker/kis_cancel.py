"""한국투자증권 KIS 해외 주문 취소 (단일 책임: 미체결 해외주문 취소).

체결 추격(kis_chase)이 미체결 잔여를 취소하고 재주문할 때 사용한다. 취소도 주문과 같은
무재시도 자금경로 — kis_order.post_order(hashkey 서명 POST)를 재사용한다. 모의/실전은
TR ID 첫 글자(V↔T)로 토글. 원주문 식별은 주문 응답 output.ODNO — 미체결조회 TR은 실전
전용이라 쓰지 않는다. 국내(KR)는 시장가만 사용해 미체결 잔존이 없으므로 취소 미구현.
출처: KIS Developers — 해외 order-rvsecncl(미국 정정취소 TTTT1004U, RVSE_CNCL_DVSN_CD 02=취소).
"""
from common.broker.kis_account import _tr, split_account
from common.broker.kis_order import post_order


def cancel_overseas_order(symbol: str, odno: str, qty: int, exchange: str) -> dict:
    """해외(미국) 미체결 주문 취소. odno=원주문번호(주문 응답 output.ODNO), qty=취소(잔여) 수량."""
    cano, prdt = split_account()
    body = {
        "CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": exchange, "PDNO": symbol,
        "ORGN_ODNO": odno, "RVSE_CNCL_DVSN_CD": "02",
        "ORD_QTY": str(qty), "OVRS_ORD_UNPR": "0",
        "ORD_SVR_DVSN_CD": "0",
    }
    return post_order("/uapi/overseas-stock/v1/trading/order-rvsecncl", _tr("TTTT1004U"), body)
