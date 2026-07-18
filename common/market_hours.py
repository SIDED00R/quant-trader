"""자산군 판정 + 시장 개장시간 (단일 책임: 심볼 → 자산군 → 거래가능시간).

심볼 형태로 자산군을 가른다 — 코인=`KRW-` 접두, 국내주식=6자리 숫자(005930), 그 외=미국주식 티커.
코인은 24/7이라 `is_market_open`이 항상 True이고, 주식만 정규장 시간을 검사한다(KRX·US).
연율화 기준(`periods_per_year`)도 자산군별로 다르다 — 코인 365×24h, 주식 252거래일×6.5h 정규장.
`common/` 소속이라 백테스트·프로덕션 양쪽에서 import 가능하다(batch 비의존 — 경계 규칙 준수).
"""
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache

_KST = timezone(timedelta(hours=9))

# KRX 정규장(streaming/ingester/stock_kiwoom.py 주석과 동일: 09:00~15:30 KST)
_KRX_OPEN = time(9, 0)
_KRX_CLOSE = time(15, 30)
# US 정규장 09:30~16:00 ET(서머타임은 zoneinfo가 처리)
_US_OPEN = time(9, 30)
_US_CLOSE = time(16, 0)

# 연율화 기준(자산군별): 거래일/년 · 활성 거래시간/일(초). 주식 정규장=6.5h=23,400초.
_TRADING_DAYS = {"COIN": 365.0, "STOCK_KR": 252.0, "STOCK_US": 252.0}
_SESSION_SECONDS = {"COIN": 86400.0, "STOCK_KR": 23400.0, "STOCK_US": 23400.0}


@lru_cache(maxsize=1)
def _et():
    """미국 동부 타임존(DST 자동). zoneinfo는 US 세션 검사 시에만 로드 — import 부작용 최소화."""
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


def asset_class(symbol: str) -> str:
    """심볼 → 'COIN' | 'STOCK_KR' | 'STOCK_US'."""
    if symbol.startswith("KRW-"):
        return "COIN"
    if symbol.isdigit():        # 국내 6자리 종목코드 005930
        return "STOCK_KR"
    return "STOCK_US"           # 그 외 영문 티커 AAPL


def is_stock(symbol: str) -> bool:
    return asset_class(symbol) in ("STOCK_KR", "STOCK_US")


def is_market_open(symbol: str, now: datetime | None = None) -> bool:
    """정규장 거래시간 여부. 코인=항상 True. 국내주식=KRX 평일 09:00–15:30 KST,
    미국주식=평일 09:30–16:00 ET(서머타임 zoneinfo 처리).

    공휴일은 여기서 보지 않는다 — 휴장 게이트는 common/market_holidays.py가 주문 경로에서 별도 담당
    (이 함수는 요일·정규장 시간만 판정). 분봉 백테스트의 정규장 필터와 라이브 주문 게이트 양쪽에서 쓴다.
    """
    kind = asset_class(symbol)
    if kind == "COIN":
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    if kind == "STOCK_KR":
        local, lo, hi = now.astimezone(_KST), _KRX_OPEN, _KRX_CLOSE
    else:                                # STOCK_US
        local, lo, hi = now.astimezone(_et()), _US_OPEN, _US_CLOSE
    if local.weekday() >= 5:             # 토(5)·일(6) 휴장
        return False
    return lo <= local.time() <= hi


def periods_per_year(symbol: str, sample_sec: float) -> float:
    """자산군 인지 연율화 계수(연간 표본 수). Sharpe/DSR 연율화용.

    = 거래일/년 × max(1, 활성거래시간/표본간격). 코인(24/7)은 기존 SECONDS_PER_YEAR/sample_sec와
    동치(무영향). 주식은 252거래일×6.5h 정규장 기준 — 분봉이면 252×390=98,280, 일봉이면 252.
    """
    kind = asset_class(symbol)
    days = _TRADING_DAYS[kind]
    sess = _SESSION_SECONDS[kind]
    obs_per_day = max(1.0, sess / sample_sec) if sample_sec > 0 else 1.0
    return days * obs_per_day


def _local_tz(symbol: str):
    """심볼의 로컬 거래 타임존(국내=KST, 미국=ET, 코인=UTC)."""
    kind = asset_class(symbol)
    return _KST if kind == "STOCK_KR" else (_et() if kind == "STOCK_US" else timezone.utc)


def session_date(symbol: str, now: datetime):
    """심볼의 로컬 거래일(date) — 인트라데이 세션 경계 감지용(KR=KST·US=ET·코인=UTC). now는 tz-aware."""
    return now.astimezone(_local_tz(symbol)).date()


def seconds_to_close(symbol: str, now: datetime) -> float | None:
    """정규장 마감까지 남은 초. 코인=None(마감 없음). 음수면 마감 후. now는 tz-aware."""
    kind = asset_class(symbol)
    if kind == "COIN":
        return None
    close = _KRX_CLOSE if kind == "STOCK_KR" else _US_CLOSE
    local = now.astimezone(_local_tz(symbol))
    close_dt = local.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
    return (close_dt - local).total_seconds()


# 시장 단위(심볼 없이) 정규장 판정용 대표 심볼 — asset_class 분기만 태운다(라이브 주문 게이트용).
_MARKET_PROXY = {"KR": "005930", "US": "SPY", "COIN": "KRW-BTC"}


def market_open(market: str, now: datetime | None = None) -> bool:
    """시장('KR'|'US'|'COIN') 정규장 개장 여부 — 종목 없이 시장 단위로 묻는다(라이브 주문 게이트).

    is_market_open을 대표 심볼로 재사용(세션 시간 로직 단일 출처).
    """
    return is_market_open(_MARKET_PROXY[market], now)


def market_seconds_to_close(market: str, now: datetime) -> float | None:
    """시장 마감까지 남은 초 — 시장 단위(코인=None, 음수면 마감 후)."""
    return seconds_to_close(_MARKET_PROXY[market], now)
