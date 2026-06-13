"""이벤트 스키마 (단일 책임: 직렬화 모델)."""
import json
from dataclasses import asdict, dataclass


@dataclass
class Tick:
    symbol: str
    price: float
    volume: float
    side: str        # BID | ASK
    trade_ts: str    # ISO8601 (UTC)
    seq: int

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")


@dataclass
class Order:
    order_id: str
    account_id: str
    symbol: str
    side: str             # BUY | SELL
    type: str             # MARKET | LIMIT
    price: float | None   # LIMIT일 때만
    quantity: float
    ts: str               # ISO8601 (UTC)

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")


@dataclass
class Execution:
    execution_id: str
    order_id: str
    account_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    fee: float
    ts: str               # ISO8601 (UTC)

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")
