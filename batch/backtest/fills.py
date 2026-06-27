"""체결 모델 (단일 책임: 체결가·수수료 산정).

라이브 engine.matching과 동일 가정:
- MARKET 주문은 트리거 시점 시장가로 즉시 체결(지연=0 이상화).
- 수수료 = 체결가 × 수량 × FEE_RATE, 0.0001 단위 양자화(engine.execute와 동일).
슬리피지는 기본 0(라이브 가정)이며, bps로 불리한 방향 슬리피지를 선택 적용할 수 있다.
"""
from dataclasses import dataclass
from decimal import Decimal

from common.config import FEE_RATE, STOCK_SELL_TAX_RATE
from common.market_hours import asset_class

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

    def tax(self, symbol: str, price: Decimal, qty: Decimal) -> Decimal:
        """매도 거래세 — 국내주식(STOCK_KR)만 STOCK_SELL_TAX_RATE 적용. 코인·미국주식=0.

        매수엔 호출하지 않아 매수/매도 비대칭은 호출측(engine.sell만 호출)에서 보장된다.
        미국은 KOSPI 거래세가 없어 0(미국 수수료/세제는 별도 — 현 단계 미모델).
        """
        if asset_class(symbol) != "STOCK_KR":
            return Decimal(0)
        return (price * qty * STOCK_SELL_TAX_RATE).quantize(QUANT_FEE)
