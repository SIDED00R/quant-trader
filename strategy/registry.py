"""전략 레지스트리 (단일 책임: 이름 → 전략 클래스 조회).

신규 전략(2단계 RSI/MACD/...)은 여기 등록만 하면 백테스트(--strategy)·앙상블에서 이름으로 선택된다.
"""
from strategy.base import Strategy
from strategy.sma import SMAStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    SMAStrategy.name: SMAStrategy,
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def get_strategy(name: str) -> Strategy:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown strategy '{name}'. available: {available()}")
    return cls()
