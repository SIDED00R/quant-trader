"""한국투자증권 KIS 계좌/잔고 조회 (단일 책임: KIS 잔고 조회).

국내·해외 잔고를 각각의 엔드포인트/TR ID로 조회한다(KIS는 시장별 API가 분리됨).
모의/실전은 TR ID 첫 글자로 토글(V=모의, T=실전). 계좌번호는 CANO(8) + 상품코드(2)로 분해.
인증 헤더(appkey/appsecret/tr_id/Bearer)는 공통 구성, 토큰은 kis_client 캐시 사용.
출처: KIS Developers — 국내 inquire-balance(TTTC8434R), 해외 inquire-balance(TTTS3012R).
"""
from common.config import (
    KIS_ACCOUNT_NO,
    KIS_APPKEY,
    KIS_APPSECRET,
    KIS_MOCK,
    KIS_REST_BASE,
)
from common.constants import (
    BROKER_TIMEOUT,
    KIS_DEFAULT_CURRENCY,
    KIS_DEFAULT_EXCHANGE,
    KIS_TR_DOMESTIC_BALANCE,
    KIS_TR_OVERSEAS_BALANCE,
)
from common.http_client import get_json
from common.broker.kis_client import get_access_token
from common.rate_limit import acquire


def to_float(x) -> float:
    """KIS 응답의 콤마 포함 숫자 문자열 → float(파싱 실패 시 0.0). 잔고/현재가 정규화 공용."""
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _tr(real_id: str) -> str:
    """실전 TR ID → 모의 TR ID(첫 글자 T→V). 실전이면 그대로."""
    return ("V" + real_id[1:]) if KIS_MOCK else real_id


def split_account() -> tuple[str, str]:
    """KIS_ACCOUNT_NO → (CANO 8자리, 상품코드 2자리). 상품코드 미지정 시 종합 '01'."""
    raw = KIS_ACCOUNT_NO.strip()
    if "-" in raw:
        cano, prdt = raw.split("-", 1)
        return cano, (prdt or "01")
    return raw[:8], (raw[8:10] or "01")


def _headers(tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": KIS_APPKEY,
        "appsecret": KIS_APPSECRET,
        "tr_id": tr_id,
        "custtype": "P",  # 개인
    }


def _get(path: str, tr_id: str, params: dict) -> dict:
    acquire("kis", "rest")   # KIS REST 호출 한도(모의 실측 2/s) 페이싱
    body = get_json(f"{KIS_REST_BASE}{path}", params, headers=_headers(tr_id), timeout=BROKER_TIMEOUT)
    if str(body.get("rt_cd")) != "0":
        raise RuntimeError(f"KIS 조회 실패({tr_id}): {body.get('msg_cd')} {body.get('msg1')}")
    return body


def _summary(body: dict) -> dict:
    """output2를 단일 계좌요약 dict로 정규화(국내=list, 해외=dict 모두 대응). 없으면 {}."""
    out2 = body.get("output2")
    if isinstance(out2, list):
        return out2[0] if out2 else {}
    return out2 if isinstance(out2, dict) else {}


def fetch_domestic_balance() -> tuple[list, dict]:
    """국내 잔고. (보유종목 output1, 계좌요약 output2) 반환(원본 dict 그대로)."""
    cano, prdt = split_account()
    body = _get(
        "/uapi/domestic-stock/v1/trading/inquire-balance",
        _tr(KIS_TR_DOMESTIC_BALANCE),
        {
            "CANO": cano, "ACNT_PRDT_CD": prdt,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        },
    )
    return body.get("output1", []), _summary(body)


def fetch_overseas_balance(exchange: str = KIS_DEFAULT_EXCHANGE, currency: str = KIS_DEFAULT_CURRENCY) -> tuple[list, dict]:
    """해외 잔고(거래소·통화별). (보유종목 output1, 계좌요약 output2) 반환(원본 dict 그대로).

    OVRS_EXCG_CD: NASD(나스닥)/NYSE/AMEX. 미국 전체 보유는 거래소별로 각각 조회 필요.
    """
    cano, prdt = split_account()
    body = _get(
        "/uapi/overseas-stock/v1/trading/inquire-balance",
        _tr(KIS_TR_OVERSEAS_BALANCE),
        {
            "CANO": cano, "ACNT_PRDT_CD": prdt,
            "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": currency,
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        },
    )
    return body.get("output1", []), _summary(body)
