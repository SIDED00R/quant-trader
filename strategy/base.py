"""전략 추상화 (단일 책임: 전략 인터페이스 + 실행 어댑터 프로토콜).

Strategy는 시장 틱과 Broker만 의존하는 순수 의사결정 단위다(Kafka/DB 비의존).
- 백테스트: BacktestEngine이 Broker를 구현(단일 계좌·동기 체결).
- 라이브(4~5단계 예정): 계좌별 Broker 어댑터가 place_order/포지션 캐시를 구현해 동일 전략을 구동.
틱은 symbol/price/ts만 요구하는 MarketTick 구조로 받는다(데이터원 비의존 — 백테스트 BTick·라이브 틱 공통).
"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Protocol


class MarketTick(Protocol):
    symbol: str
    price: Decimal
    ts: float       # epoch seconds (백테스트=시장 가상시계 / 라이브=실시간)


class Broker(Protocol):
    """전략이 의존하는 동기 체결·상태 조회 인터페이스."""
    def position_qty(self, symbol: str) -> Decimal: ...
    def position_avg(self, symbol: str) -> Decimal: ...
    def cash(self) -> Decimal: ...
    def open_symbol_count(self) -> int: ...
    def buy(self, symbol: str, qty: Decimal, ts: float) -> bool: ...
    def sell(self, symbol: str, qty: Decimal, reason: str, ts: float) -> bool: ...


class Strategy(ABC):
    """매 틱 시장 데이터를 받아 Broker로 매매를 지시하는 전략 단위.

    구현체는 자체 인메모리 상태(지표 윈도우·추세·쿨다운 등)를 들고, 부작용은 broker 호출로만 낸다.
    """
    name: str = "base"

    @abstractmethod
    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        ...
