"""백테스트 값 타입 (단일 책임: 데이터 구조 정의).

금액·수량·가격은 라이브와 동일하게 Decimal로 다룬다(부동소수 오차 회피).
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BTick:
    """replay되는 한 건의 체결 틱(시장시간 기준)."""
    symbol: str
    price: Decimal
    ts: float       # epoch seconds (trade_ts → 시장 가상시계)


@dataclass
class ClosedTrade:
    """완결된 1회 라운드트립(매수→전량 매도). 수익률/승률 집계 단위."""
    symbol: str
    qty: Decimal
    entry_price: Decimal    # 진입 체결가(원가, 수수료 제외)
    exit_price: Decimal     # 청산 체결가
    buy_fee: Decimal
    sell_fee: Decimal
    pnl: Decimal            # 실현 손익(매수·매도 수수료·매도세 모두 반영)
    return_pct: Decimal     # pnl / 취득원가(수수료 포함 평단 기준)
    reason: str             # STOP | TAKE | TRAIL | DEADCROSS
    entry_ts: float
    exit_ts: float
    sell_tax: Decimal = Decimal(0)   # 매도 거래세(국내주식만 >0, 코인/미국=0). 기본값=기존 호출 무영향

    @property
    def holding_sec(self) -> float:
        return self.exit_ts - self.entry_ts
