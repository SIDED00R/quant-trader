"""KIS 국내 현재가 (단일 책임: KR 종목 현재가 조회 — 수동주문 금액→수량 환산용).

kis_overseas_price(US판)의 KR 대응. 시세 TR(FHKST01010100)은 주문·잔고 TR과 달리
모의/실전 변형이 없다(V토글 불요). 실패·미확인은 None — 호출측이 저장 종가로 폴백.
"""
from common.config import KIS_REST_BASE
from common.constants import BROKER_TIMEOUT
from common.http_client import get_json
from common.broker.kis_account import _headers, to_float as _f
from common.rate_limit import acquire


def current_price(symbol: str) -> float | None:
    """현재가(원) 또는 None."""
    acquire("kis", "rest")
    try:
        body = get_json(
            f"{KIS_REST_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
            headers=_headers("FHKST01010100"), timeout=BROKER_TIMEOUT)
    except Exception:
        return None
    if str(body.get("rt_cd")) != "0":
        return None
    px = _f((body.get("output") or {}).get("stck_prpr"))
    return px if px > 0 else None
