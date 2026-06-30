"""한국투자증권 KIS 단건 주문 (단일 책임: KR/US 모의 주문 생성).

주문은 시장별 엔드포인트/TR이 분리된다 — 국내 order-cash, 해외 order. 종목 market
(KR=6자리 숫자 / US=티커)으로 라우팅한다. 주문 POST는 hashkey 헤더로 본문 무결성을
보장하고 **재시도하지 않는다**(중복 주문 방지 — GET 조회와 다름). 모의/실전은 TR ID
첫 글자(V↔T)로 토글. 인증헤더·계좌분해·TR토글은 kis_account의 헬퍼를 재사용한다.
출처: KIS Developers — 국내 order-cash(매수 TTTC0802U/매도 TTTC0801U),
해외 order(미국 매수 TTTT1002U/매도 TTTT1006U), hashkey(/uapi/hashkey).
"""
import httpx

from common.config import KIS_APPKEY, KIS_APPSECRET, KIS_REST_BASE
from common.constants import BROKER_TIMEOUT, KIS_DEFAULT_EXCHANGE
from common.kis_account import _headers, _tr, split_account
from common.rate_limit import acquire


def _is_kr(symbol: str) -> bool:
    """KR=6자 종목코드(숫자 포함, 영숫자 단축코드 0009K0 등 포함), US=영문 티커."""
    return len(symbol) == 6 and any(c.isdigit() for c in symbol)


def _validate(side: str, qty: int) -> None:
    """주문 안전 가드 — 무재시도 자금경로라 무음 폴백 방지."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side는 BUY|SELL만 허용 — got {side!r}")
    if qty <= 0:
        raise ValueError(f"qty는 양수여야 함 — got {qty}")


def _hashkey(body: dict) -> str:
    """주문 본문 → HASH (POST /uapi/hashkey). 주문 POST의 hashkey 헤더로 사용."""
    acquire("kis", "rest")
    r = httpx.post(
        f"{KIS_REST_BASE}/uapi/hashkey",
        headers={"content-type": "application/json", "appkey": KIS_APPKEY, "appsecret": KIS_APPSECRET},
        json=body,
        timeout=BROKER_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["HASH"]


def _post_order(path: str, tr_id: str, body: dict) -> dict:
    """주문 POST(재시도 없음). rt_cd!=0이면 사유와 함께 예외. output(ODNO 등) 포함 응답 반환."""
    headers = _headers(tr_id)
    headers["hashkey"] = _hashkey(body)
    acquire("kis", "rest")
    r = httpx.post(f"{KIS_REST_BASE}{path}", headers=headers, json=body, timeout=BROKER_TIMEOUT)
    r.raise_for_status()
    b = r.json()
    if str(b.get("rt_cd")) != "0":
        raise RuntimeError(f"KIS 주문 실패({tr_id}): {b.get('msg_cd')} {b.get('msg1')}")
    return b


def place_domestic_order(symbol: str, side: str, qty: int, price: int | None = None) -> dict:
    """국내 현금주문. price 있으면 지정가(00), 없으면 시장가(01). side: BUY|SELL."""
    _validate(side, qty)
    cano, prdt = split_account()
    tr = _tr("TTTC0802U" if side == "BUY" else "TTTC0801U")
    body = {
        "CANO": cano, "ACNT_PRDT_CD": prdt, "PDNO": symbol,
        "ORD_DVSN": "00" if price is not None else "01",
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(price) if price is not None else "0",
    }
    return _post_order("/uapi/domestic-stock/v1/trading/order-cash", tr, body)


def place_overseas_order(symbol: str, side: str, qty: int, price, exchange: str = KIS_DEFAULT_EXCHANGE) -> dict:
    """해외(미국) 지정가 주문. 미국은 지정가만 — price 필수. side: BUY|SELL."""
    _validate(side, qty)
    cano, prdt = split_account()
    tr = _tr("TTTT1002U" if side == "BUY" else "TTTT1006U")
    body = {
        "CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": exchange, "PDNO": symbol,
        "ORD_QTY": str(qty), "OVRS_ORD_UNPR": str(price),
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",
    }
    return _post_order("/uapi/overseas-stock/v1/trading/order", tr, body)


def place_order(symbol: str, side: str, qty: int, price=None, exchange: str = KIS_DEFAULT_EXCHANGE) -> dict:
    """종목 market으로 자동 라우팅 — KR=국내(지정/시장), US=해외(지정가, price 필수)."""
    if _is_kr(symbol):
        return place_domestic_order(symbol, side, qty, price)
    if price is None:
        raise ValueError("해외(US) 주문은 지정가만 지원 — price 필수")
    return place_overseas_order(symbol, side, qty, price, exchange)
