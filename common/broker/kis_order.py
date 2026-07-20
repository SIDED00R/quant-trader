"""한국투자증권 KIS 단건 주문 (단일 책임: KR/US 모의 주문 생성).

주문은 시장별 엔드포인트/TR이 분리된다 — 국내 order-cash, 해외 order. 호출 측이 시장에
맞는 place_domestic_order(국내)/place_overseas_order(해외)를 직접 호출한다. 주문 POST는 hashkey 헤더로 본문 무결성을
보장하고 **재시도하지 않는다**(중복 주문 방지 — GET 조회와 다름). 단 하나의 예외:
EGW00201(게이트웨이 초당 한도 거부)은 업무서버 도달 전 차단 = 주문 미접수가 확실하므로
백오프 후 재시도한다. 모의/실전은 TR ID 첫 글자(V↔T)로 토글. 인증헤더·계좌분해·TR토글은
kis_account의 헬퍼를 재사용한다.
출처: KIS Developers — 국내 order-cash(매수 TTTC0802U/매도 TTTC0801U),
해외 order(미국 매수 TTTT1002U/매도 TTTT1006U), hashkey(/uapi/hashkey).
"""
import time

import httpx

from common.config import KIS_APPKEY, KIS_APPSECRET, KIS_REST_BASE
from common.constants import BROKER_TIMEOUT, KIS_DEFAULT_EXCHANGE
from common.broker.kis_account import _headers, _tr, split_account
from common.rate_limit import acquire

_THROTTLE_CD = "EGW00201"      # 초당 거래건수 초과 — 게이트웨이 거부(주문 미접수 확실)라 재시도 안전
_RETRY_WAITS = (1.0, 2.0)      # 재시도 전 대기(초) — post_order는 EGW00201 한정, hashkey는 5xx/전송오류. 총 시도 = len+1회


def _validate(side: str, qty: int) -> None:
    """주문 안전 가드 — 무재시도 자금경로라 무음 폴백 방지."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side는 BUY|SELL만 허용 — got {side!r}")
    if qty <= 0:
        raise ValueError(f"qty는 양수여야 함 — got {qty}")


def _safe_json(r) -> dict:
    """응답 JSON(비JSON이면 {}) — 4xx/5xx body의 msg_cd/msg1을 raise 전에 캡처."""
    try:
        b = r.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _hashkey(body: dict) -> str:
    """주문 본문 → HASH (POST /uapi/hashkey). 주문 POST의 hashkey 헤더로 사용.

    순수 해시 계산(부수효과 없음 = 멱등)이라 5xx/전송오류는 짧게 재시도한다. 4xx는 즉시 실패.
    """
    last = ""
    for i in range(len(_RETRY_WAITS) + 1):
        acquire("kis", "rest")
        try:
            r = httpx.post(
                f"{KIS_REST_BASE}/uapi/hashkey",
                headers={"content-type": "application/json", "appkey": KIS_APPKEY, "appsecret": KIS_APPSECRET},
                json=body,
                timeout=BROKER_TIMEOUT,
            )
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()["HASH"]
            b = _safe_json(r)
            last = f"HTTP {r.status_code} {b.get('msg_cd')} {b.get('msg1') or r.text[:120]}"
        except httpx.TransportError as e:
            last = f"{type(e).__name__}: {e}"
        if i < len(_RETRY_WAITS):
            print(f"[kis-order] hashkey 실패 재시도 {i + 1}/{len(_RETRY_WAITS)}: {last}")
            time.sleep(_RETRY_WAITS[i])
    raise RuntimeError(f"KIS hashkey 실패(재시도 소진): {last}")


def post_order(path: str, tr_id: str, body: dict) -> dict:
    """주문 POST. 원칙 무재시도(비멱등 자금경로) — 단 EGW00201(게이트웨이 초당 한도 거부 =
    미접수 확실)만 백오프 재시도(총 3회). 그 외 4xx/5xx·전송오류는 접수 여부 불명이므로
    즉시 예외(중복 주문 방지 — 체결 여부는 호출측 잔고 diff가 판정). 4xx/5xx body의
    msg_cd/msg1은 예외 메시지에 포함한다(2026-07-20 사고: raise가 body 파싱보다 먼저라
    EGW00201 원인이 로그에 안 남았음). rt_cd!=0이면 사유와 함께 예외. output(ODNO 등) 포함 응답 반환.

    취소(kis_cancel)도 같은 자금경로라 이 헬퍼를 재사용한다(EGW00201 재시도 동일 적용).
    """
    headers = _headers(tr_id)
    headers["hashkey"] = _hashkey(body)   # body 불변 → 해시 1회 계산, 재시도에 재사용
    for i in range(len(_RETRY_WAITS) + 1):
        acquire("kis", "rest")
        r = httpx.post(f"{KIS_REST_BASE}{path}", headers=headers, json=body, timeout=BROKER_TIMEOUT)
        b = _safe_json(r)
        throttled = b.get("msg_cd") == _THROTTLE_CD   # 500이든 200이든 게이트웨이 거부 = 미접수
        if throttled and i < len(_RETRY_WAITS):
            print(f"[kis-order] 초당 한도 거부({tr_id}) 재시도 {i + 1}/{len(_RETRY_WAITS)}: "
                  f"HTTP {r.status_code} {b.get('msg1')}")
            time.sleep(_RETRY_WAITS[i])
            continue
        exhausted = " — 한도거부 재시도 소진" if throttled else ""
        if r.status_code >= 400:
            raise RuntimeError(f"KIS 주문 HTTP {r.status_code}({tr_id}): {b.get('msg_cd')} "
                               f"{b.get('msg1') or r.text[:200]}{exhausted}")
        if str(b.get("rt_cd")) != "0":
            raise RuntimeError(f"KIS 주문 실패({tr_id}): {b.get('msg_cd')} {b.get('msg1')}{exhausted}")
        return b
    raise AssertionError("unreachable — post_order 루프는 return/raise로만 종료")


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
    return post_order("/uapi/domestic-stock/v1/trading/order-cash", tr, body)


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
    return post_order("/uapi/overseas-stock/v1/trading/order", tr, body)
