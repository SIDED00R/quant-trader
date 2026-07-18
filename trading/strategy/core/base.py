"""전략 추상화 (단일 책임: 전략 인터페이스 + 실행 어댑터 프로토콜).

Strategy는 시장 틱과 Broker만 의존하는 순수 의사결정 단위다(Kafka/DB 비의존).
- 백테스트: BacktestEngine이 Broker를 구현(단일 계좌·동기 체결).
- 라이브: 앙상블 경로(live_ensemble→commander / trade_once)가 전략의 순수 판정을 재사용해 동기 체결한다.
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
    def equity(self) -> Decimal: ...           # 현금+보유평가 총액(변동성 타게팅 사이징용)
    def open_symbol_count(self) -> int: ...
    def iter_positions(self) -> list[str]: ...   # 보유(수량>0) 심볼 목록 — 횡단면 청산 대상 식별
    def price(self, symbol: str) -> Decimal: ...  # 현재 시장가(체결 기준가) — 사이징=체결 일치용
    def buy(self, symbol: str, qty: Decimal, ts: float, price: Decimal | None = None) -> bool: ...
    def sell(self, symbol: str, qty: Decimal, reason: str, ts: float, price: Decimal | None = None) -> bool: ...


class Strategy(ABC):
    """매 틱 시장 데이터를 받아 Broker로 매매를 지시하는 전략 단위.

    구현체는 자체 인메모리 상태(지표 윈도우·추세·쿨다운 등)를 들고, 부작용은 broker 호출로만 낸다.
    """
    name: str = "base"

    @abstractmethod
    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        ...
