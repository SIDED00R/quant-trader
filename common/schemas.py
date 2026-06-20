"""이벤트 스키마 (단일 책임: 직렬화 모델).

금액·수량은 Decimal로 다룬다. JSON에는 문자열로 직렬화(default=str)하고,
소비 측에서 Decimal로 복원해 부동소수 오차 없이 NUMERIC까지 전달한다.
"""
import json
from dataclasses import asdict, dataclass
from decimal import Decimal


def _dumps(obj) -> bytes:
    return json.dumps(asdict(obj), default=str).encode("utf-8")


@dataclass
class Tick:
    symbol: str
    price: Decimal
    volume: Decimal
    side: str        # BID | ASK
    trade_ts: str    # ISO8601 (UTC)
    seq: int

    def to_json(self) -> bytes:
        return _dumps(self)


@dataclass
class Order:
    order_id: str
    account_id: str
    symbol: str
    side: str               # BUY | SELL
    type: str               # MARKET | LIMIT
    price: Decimal | None   # LIMIT일 때만
    quantity: Decimal
    ts: str                 # ISO8601 (UTC)

    def to_json(self) -> bytes:
        return _dumps(self)


@dataclass
class Signal:
    """전략 부하 → commander 신호(strategy.signals). target_weight=목표 비중(0~max, 0=현금)."""
    symbol: str
    strategy: str           # 전략명(예: 'ensemble')
    target_weight: Decimal  # 합성 목표 비중(0=현금)
    bar_ts: str             # 신호 산출 봉(일봉) 시각 ISO8601(UTC)
    ts: str                 # 발행 시각 ISO8601(UTC)

    def to_json(self) -> bytes:
        return _dumps(self)


@dataclass
class Execution:
    execution_id: str
    order_id: str
    account_id: str
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    ts: str                 # ISO8601 (UTC)

    def to_json(self) -> bytes:
        return _dumps(self)
