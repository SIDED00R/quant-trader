"""외부 API 호출 한도 통합 관리 (단일 책임: 클라이언트측 레이트리밋).

제공자×그룹별 토큰버킷으로 초당 호출량을 제한한다. 각 클라이언트는 요청 직전
`acquire(provider, group)`를 호출하고, 토큰이 없으면 충전될 때까지 블록(sleep)된다.
한도 표는 docs/rate_limits.md 참조(출처·신뢰도 포함).

⚠️ 인프로세스 한정 — 같은 자격증명(appkey 등)을 여러 프로세스/VM이 동시에 쓰면 전역
한도를 보장하지 못한다. 일 1회 배치·단일 프로세스 백필엔 충분하며, 다중 프로세스 공유가
필요해지면 Redis 백엔드 토큰버킷으로 교체한다(Kafka/Airflow는 레이트리밋 도구가 아님).
"""
import threading
import time

# 제공자:그룹 → 초당 허용 요청 수(req/s). 보수적 기본값(출처: docs/rate_limits.md).
# 동적/불명확한 값은 보수적으로 잡고, 호출부에서 rate= 로 덮어쓸 수 있다.
_DEFAULT_RATES: dict[str, float] = {
    # Upbit (공식)
    "upbit:quotation": 10,   # 시세, IP 기준 (분당 600)
    "upbit:order": 8,        # 주문, 키 기준 (분당 200)
    "upbit:exchange": 30,    # 주문 외 Exchange, 키 기준 (분당 900)
    # KIS (공식): 모의 5 / 실전 20. 기본은 모의 — 실전은 acquire(..., rate=20).
    "kis:rest": 5,
    # Toss (관측/스펙): 그룹별. 정확값은 응답 X-RateLimit-* 헤더로 확인 권장.
    "toss:AUTH": 5,
    "toss:MARKET_DATA": 5,
    "toss:MARKET_DATA_CHART": 5,
    # Kiwoom (커뮤니티 추정): TR별 ~1/s. 보수적.
    "kiwoom:tr": 1,
    # Telegram (공식: 개별 채팅 ~1 msg/s): 매매 알림은 하루 수 건이라 보수값으로 충분.
    "telegram:send": 1,
}


class TokenBucket:
    """초당 rate개로 충전되는 토큰버킷. acquire(n)은 토큰이 찰 때까지 블록한다."""

    def __init__(self, rate: float, capacity: float | None = None):
        self.rate = float(rate)                       # 초당 충전 토큰 수
        # capacity 기본 1 = 버스트 없는 균등 페이싱. 토큰버킷 cap=rate면 고정 1초 윈도우
        # 경계에서 최대 ~2×rate까지 통과할 수 있어, 엄격 한도(예: KIS 실전 20)에선 순간 초과 위험.
        # 버스트가 필요하면 capacity를 명시한다.
        self.capacity = float(capacity if capacity is not None else 1.0)  # 버스트 상한
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: int = 1) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self.rate
            time.sleep(wait)   # 락 밖에서 대기 — 다른 버킷/스레드 진행 허용


_limiters: dict[str, TokenBucket] = {}
_reg_lock = threading.Lock()


def limiter(provider: str, group: str, rate: float | None = None) -> TokenBucket:
    """(provider, group)용 공유 토큰버킷 반환. 최초 호출 시 rate(또는 기본표)로 생성."""
    key = f"{provider}:{group}"
    with _reg_lock:
        bucket = _limiters.get(key)
        if bucket is None:
            r = rate if rate is not None else _DEFAULT_RATES.get(key)
            if r is None:
                raise KeyError(f"등록되지 않은 한도: {key} — acquire(rate=...)로 지정하거나 _DEFAULT_RATES에 추가")
            bucket = TokenBucket(r)
            _limiters[key] = bucket
        return bucket


def acquire(provider: str, group: str, n: int = 1, rate: float | None = None) -> None:
    """요청 직전 호출 — 한도 내 토큰이 확보될 때까지 블록한다.

    rate는 해당 (provider, group) 버킷 **최초 생성 시에만** 반영된다(first-wins). 이후 다른
    rate로 호출해도 무시되므로, 모드(모의/실전)별 한도가 다르면 group을 분리한다
    (예: 'rest-mock' / 'rest-real').
    """
    limiter(provider, group, rate).acquire(n)
