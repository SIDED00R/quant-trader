"""세션(거래일) 기준 인트라데이 단일종목 전략 (단일 책임: 세션 추적 + 오버나잇 미보유 long-or-cash).

research §2.1/§2.3 shortlist. 정규장 분봉을 받아 **세션 안에서만 보유**하고 마감 봉에서 청산(오버나잇 위험 제거).
한 세션 1회 진입(long-or-cash, 공매도 없음). 베이스가 세션 경계·개장 레인지·마감 청산을 처리하고,
서브클래스는 `_should_enter(price, session)`만 구현(신호 교체점). 다종목 스트림에서 per-symbol 세션 상태.

체결은 봉 가격(tick.price) 명시 전달(횡단면과 동일 — 체결가 일관). run.py가 `configure(bar_min)`로 봉 간격 주입.
주의: 연구상 단일종목 인트라데이는 비용에 약함 — 검증(walk-forward+비용게이트)으로 채택 여부 판정.
"""
from abc import abstractmethod
from datetime import datetime, timezone
from decimal import Decimal

from common.config import (
    MIN_ORDER_KRW,
    MOM_SIGNAL_BARS,
    MOM_THRESHOLD,
    ORB_OPENING_BARS,
    STRATEGY_ORDER_FRACTION_MAX,
)
from common.market_hours import seconds_to_close, session_date
from trading.strategy.base import Broker, MarketTick, Strategy


class _Session:
    """한 종목·한 거래일의 세션 상태(개장 레인지·개장 N봉 종가·진입 여부)."""
    __slots__ = ("date", "first", "n", "or_hi", "or_lo", "window_close", "window_done", "entered")

    def __init__(self, d, first_price):
        self.date = d
        self.first = first_price
        self.n = 0
        self.or_hi = None
        self.or_lo = None
        self.window_close = None
        self.window_done = False
        self.entered = False

    def update(self, price, opening_bars: int) -> None:
        self.n += 1
        if self.n <= opening_bars:                 # 개장 레인지 구간
            self.or_hi = price if self.or_hi is None else max(self.or_hi, price)
            self.or_lo = price if self.or_lo is None else min(self.or_lo, price)
            if self.n == opening_bars:
                self.window_close = price
                self.window_done = True


class IntradaySessionStrategy(Strategy):
    """세션 기준 인트라데이 베이스. 서브클래스는 `opening_bars`와 `_should_enter`만 정의."""
    opening_bars = 30

    def __init__(self, bar_min: int = 1, opening_bars: int | None = None,
                 order_fraction: Decimal | None = None):
        self.bar_sec = float(bar_min) * 60.0
        if opening_bars is not None:
            self.opening_bars = int(opening_bars)
        self.order_fraction = order_fraction if order_fraction is not None else STRATEGY_ORDER_FRACTION_MAX
        self.sessions: dict[str, _Session] = {}

    def configure(self, bar_min: int) -> None:
        self.bar_sec = float(bar_min) * 60.0

    @property
    def warmup_bars(self) -> int:
        return self.opening_bars + 1

    @abstractmethod
    def _should_enter(self, price: Decimal, s: _Session) -> bool:
        """세션 내 진입 신호(개장 윈도우 확정 후). 이미 진입/보유면 호출 안 됨."""

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        sym, price, ts = tick.symbol, tick.price, tick.ts
        now = datetime.fromtimestamp(ts, timezone.utc)
        d = session_date(sym, now)
        s = self.sessions.get(sym)
        if s is None or s.date != d:                  # 새 세션 → 전일 잔여 청산(오버나잇 금지 안전망)
            if broker.position_qty(sym) > 0:
                broker.sell(sym, broker.position_qty(sym), "SESSION_END", ts, price)
            s = _Session(d, price)
            self.sessions[sym] = s
        s.update(price, self.opening_bars)

        ttc = seconds_to_close(sym, now)              # 마감 봉(잔여 ≤ 1봉)이면 청산 후 당일 종료
        if ttc is not None and ttc <= self.bar_sec:
            if broker.position_qty(sym) > 0:
                broker.sell(sym, broker.position_qty(sym), "SESSION_CLOSE", ts, price)
            s.entered = True                          # 당일 재진입 차단
            return

        if not s.entered and broker.position_qty(sym) == 0 and self._should_enter(price, s):
            budget = broker.cash() * self.order_fraction if broker.cash() > 0 else Decimal(0)
            if budget < MIN_ORDER_KRW:
                return
            qty = (budget / price).quantize(Decimal("0.00000001"))
            if qty > 0 and broker.buy(sym, qty, ts, price):
                s.entered = True


class ORBStrategy(IntradaySessionStrategy):
    """Opening Range Breakout — 개장 N봉 고가 상향 돌파 시 매수, 세션 마감 청산(research §2.3 필터형 ORB의 단일종목 기본형)."""
    name = "orb"
    opening_bars = ORB_OPENING_BARS

    def _should_enter(self, price: Decimal, s: _Session) -> bool:
        return s.window_done and s.or_hi is not None and price > s.or_hi


class IntradayMomentumStrategy(IntradaySessionStrategy):
    """인트라데이 모멘텀 — 개장 N봉 수익률이 임계 초과면 매수해 세션 보유(research §2.1 early-session 모멘텀)."""
    name = "intraday_momentum"
    opening_bars = MOM_SIGNAL_BARS

    def __init__(self, bar_min: int = 1, opening_bars: int | None = None,
                 order_fraction: Decimal | None = None, threshold: Decimal | None = None):
        super().__init__(bar_min=bar_min, opening_bars=opening_bars, order_fraction=order_fraction)
        self.threshold = threshold if threshold is not None else MOM_THRESHOLD

    def _should_enter(self, price: Decimal, s: _Session) -> bool:
        if not s.window_done or s.first is None or s.first <= 0 or s.window_close is None:
            return False
        return (s.window_close / s.first - 1) > self.threshold
