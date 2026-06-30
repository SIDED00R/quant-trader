"""KIS 해외 현재가·거래소 해석 (단일 책임: US 티커 → 현재가 + 주문용 거래소코드).

해외 지정가주문(place_overseas_order)은 OVRS_EXCG_CD(NASD/NYSE/AMEX)가 필요한데 우리 데이터엔
거래소 정보가 없다. KIS 해외 현재가(HHDFS00000300)를 시세 거래소코드(NAS/NYS/AMS)로 순차 조회해
가격이 잡히는 거래소를 그 종목의 거래소로 보고 주문코드로 매핑한다.
⚠ 미검증: KIS 모의는 시세를 실전 도메인에서만 줄 수 있고 US장 시간에만 정확 — US장 시간 실측 필요.
"""
from common.config import KIS_REST_BASE
from common.constants import BROKER_TIMEOUT
from common.http_client import get_json
from common.kis_account import _headers
from common.rate_limit import acquire

# 시세 거래소코드(NAS/NYS/AMS) → 주문 거래소코드(NASD/NYSE/AMEX)
_EXCD_TO_ORDER = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}


def _f(x) -> float:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def price_and_exchange(symbol: str):
    """(현재가, 주문용 거래소코드) 또는 (None, None). NAS→NYS→AMS 순차 조회."""
    for excd, order_cd in _EXCD_TO_ORDER.items():
        acquire("kis", "rest")
        try:
            body = get_json(
                f"{KIS_REST_BASE}/uapi/overseas-price/v1/quotations/price",
                {"AUTH": "", "EXCD": excd, "SYMB": symbol},
                headers=_headers("HHDFS00000300"), timeout=BROKER_TIMEOUT)
        except Exception:
            continue
        if str(body.get("rt_cd")) == "0":
            last = _f((body.get("output") or {}).get("last"))
            if last > 0:
                return last, order_cd
    return None, None
