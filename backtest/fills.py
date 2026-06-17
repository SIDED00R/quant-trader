"""체결 모델 (단일 책임: 체결가·수수료 산정).

라이브 engine.matching과 동일 가정:
- MARKET 주문은 트리거 시점 시장가로 즉시 체결(지연=0 이상화).
- 수수료 = 체결가 × 수량 × FEE_RATE, 0.0001 단위 양자화(engine.execute와 동일).
슬리피지는 기본 0(라이브 가정)이며, bps로 불리한 방향 슬리피지를 선택 적용할 수 있다.
"""
from dataclasses import dataclass
from decimal import Decimal

from common.config import FEE_RATE

QUANT_FEE = Decimal("0.0001")  # engine.execute의 수수료 양자화 단위와 일치


@dataclass
class FillModel:
    fee_rate: Decimal = FEE_RATE
    slippage_bps: Decimal = Decimal(0)   # 불리한 방향 슬리피지(basis points). 0 = 라이브 가정

    def __post_init__(self):
        if self.slippage_bps < 0:   # 음수는 '유리한' 슬리피지가 되어 계약(불리한 방향)에 위배
            raise ValueError("slippage_bps must be >= 0 (불리한 방향만 적용)")

    def fill_price(self, side: str, market_price: Decimal) -> Decimal:
        if self.slippage_bps == 0:
            return market_price
        adj = self.slippage_bps / Decimal(10000)
        if side == "BUY":
            return market_price * (Decimal(1) + adj)   # 매수는 더 비싸게
        return market_price * (Decimal(1) - adj)       # 매도는 더 싸게

    def fee(self, price: Decimal, qty: Decimal) -> Decimal:
        return (price * qty * self.fee_rate).quantize(QUANT_FEE)
