"""거래소 휴장일 + 거래소 로컬 거래일 (단일 책임: market → 오늘 거래일·휴장 여부).

주간 리밸런싱의 휴장 게이트가 쓴다 — 휴장일이면 주문을 내보내지 않고 다음 평일 재시도.
의존성 0(하드코딩 셋)이라 `common/` 경계 유지(프로덕션 이미지도 import 가능). 누락된 연도·날짜는
호출처의 '체결기반 재시도'로 안전 degrade(주문→거부→다음날 재시도, 최악=1일 지연)하므로 치명적이지 않다.

NYSE(US) 전일 휴장만 수록(반일장 제외) — 연 1회 갱신. KR(KRX)은 음력(설·추석)·대체공휴일·선거일 등
공표 전에는 산정 오류 위험이 커, 공표된 연도만 수록하고 미수록 연도·누락 날짜는 체결기반 재시도에
맡긴다(셋을 채우면 자동 적용, 최악=1일 지연).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

_TZ = {"US": ZoneInfo("America/New_York"), "KR": ZoneInfo("Asia/Seoul")}

# NYSE 전일 휴장일(관측일 반영). 2025–2027 — 매년 1월 갱신.
_NYSE = {
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}

# KRX 2026 평일 휴장일(설·추석 연휴, 대체공휴일, 6/3 지방선거, 12/31 연말휴장 포함) — 매년 갱신(음력·선거일은 공표 후 반영)
_KRX = {
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 3, 2), date(2026, 5, 1), date(2026, 5, 5), date(2026, 5, 25),
    date(2026, 6, 3), date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25),
    date(2026, 10, 5), date(2026, 10, 9), date(2026, 12, 25), date(2026, 12, 31),
}

# market → 전일 휴장일 셋. 미수록 연도·날짜는 체결기반 재시도에 위임(위 docstring 참고).
_HOLIDAYS = {"US": _NYSE, "KR": _KRX}


def market_today(market: str) -> date:
    """현재 시각 기준 거래소 로컬 거래일(US=ET·KR=KST). DST는 zoneinfo가 처리."""
    return datetime.now(_TZ[market]).date()


def is_market_holiday(market: str, d: date) -> bool:
    """해당 시장의 전일 휴장일 여부(주말은 별도 — 스케줄이 평일만 발화). 미수록 날짜=개장 취급."""
    return d in _HOLIDAYS.get(market, set())
