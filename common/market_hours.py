"""자산군 판정 + 시장 개장시간 (단일 책임: 심볼 → 자산군 → 거래가능시간).

심볼 형태로 자산군을 가른다 — 코인=`KRW-` 접두, 국내주식=6자리 숫자(005930), 그 외=미국주식 티커.
코인은 24/7이라 `is_market_open`이 항상 True이고, 주식만 정규장 시간을 검사한다.
`common/` 소속이라 백테스트·프로덕션 양쪽에서 import 가능하다(batch 비의존 — 경계 규칙 준수).
"""
from datetime import datetime, time, timedelta, timezone

_KST = timezone(timedelta(hours=9))

# KRX 정규장(streaming/ingester/stock_kiwoom.py 주석과 동일: 09:00~15:30 KST)
_KRX_OPEN = time(9, 0)
_KRX_CLOSE = time(15, 30)


def asset_class(symbol: str) -> str:
    """심볼 → 'COIN' | 'STOCK_KR' | 'STOCK_US'."""
    if symbol.startswith("KRW-"):
        return "COIN"
    if symbol.isdigit():        # 국내 6자리 종목코드 005930
        return "STOCK_KR"
    return "STOCK_US"           # 그 외 영문 티커 AAPL


def is_coin(symbol: str) -> bool:
    return asset_class(symbol) == "COIN"


def is_stock(symbol: str) -> bool:
    return asset_class(symbol) in ("STOCK_KR", "STOCK_US")


def is_market_open(symbol: str, now: datetime | None = None) -> bool:
    """거래 가능 시간 여부. 코인=항상 True. 국내주식=KRX 평일 09:00–15:30 KST.

    미국주식(STOCK_US)은 서머타임·휴장일 처리가 필요해 본 단계에선 미지원 → False(주문 거부).
    공휴일 캘린더도 미반영(라이브 검증 단계에서 도입 — stock_kiwoom.py의 '라이브 후 도입' 패턴과 일관).
    백테스트는 과거 틱 재생이라 이 함수를 쓰지 않는다 — 라이브 주문 게이트(7단계 #5)용이다.
    """
    kind = asset_class(symbol)
    if kind == "COIN":
        return True
    if kind == "STOCK_KR":
        if now is None:
            now = datetime.now(timezone.utc)
        kst = now.astimezone(_KST)
        if kst.weekday() >= 5:          # 토(5)·일(6) 휴장
            return False
        return _KRX_OPEN <= kst.time() <= _KRX_CLOSE
    return False                         # STOCK_US: 미지원(후속 확장)
