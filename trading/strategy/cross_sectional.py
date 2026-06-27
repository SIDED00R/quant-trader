"""횡단면(cross-sectional) 전략 (단일 책임: 매 봉 전 종목 랭킹 → 상위 N 동일가중 long-or-cash).

대규모 유니버스에서 종목 간 상대비교로 알파를 얻는 계열(research §2.4 — 롱 다리가 long-only 제약에 강건).
엔진 run()은 변경하지 않는다(walk-forward 재사용 유지): on_tick에서 봉 경계를 감지해 직전 봉을 확정·리밸런싱한다.
봉N의 신호를 **봉N 종가(snapshot)로 전 종목 일괄 체결**(buy/sell에 명시가 전달) — 동일 시점 cross-section이라 틱
도착순·체결가 스큐·룩어헤드 영향이 없다(체결 현실성·슬리피지는 검증 단계에서 보수 적용).
리밸런싱은 공용 `rebalance.decide`(저회전 밴드·long-or-cash) 재사용 → 라이브 commander와 동일 수학.
서브클래스는 `rank()`만 구현한다(리버설/모멘텀/ML 랭킹 교체점 — trend_signal의 신호/실행 분리 철학과 동일).

데이터 가정: 데이터소스가 (symbol, window_start)별 1봉을 시간순으로 yield(분봉 candle replay) → 틱 1개 = 봉 1개.
"""
from abc import abstractmethod
from collections import deque
from decimal import Decimal

from common.config import XS_LOOKBACK, XS_MAX_WEIGHT, XS_REBALANCE_BAND, XS_TOP_N
from trading.strategy.base import Broker, MarketTick, Strategy
from trading.strategy.rebalance import bar_key, decide


class CrossSectionalStrategy(Strategy):
    """봉 단위 횡단면 랭킹 전략 베이스. `rank()`만 추상 — 신호 교체점."""

    def __init__(self, bar_min: int = 1, lookback: int | None = None, top_n: int | None = None,
                 rebalance_band: float | None = None, max_weight: Decimal | None = None):
        self.bar_sec = float(bar_min) * 60.0
        self.lookback = int(lookback if lookback is not None else XS_LOOKBACK)
        self.top_n = int(top_n if top_n is not None else XS_TOP_N)
        self.band = float(rebalance_band if rebalance_band is not None else XS_REBALANCE_BAND)
        self.max_weight = max_weight if max_weight is not None else XS_MAX_WEIGHT
        self.cur_bar: int | None = None
        self.snapshot: dict[str, Decimal] = {}     # 현재 봉의 종목별 최신가(봉 확정 시 비움)
        self.history: dict[str, deque] = {}        # 종목별 봉 종가 윈도우(maxlen=warmup_bars)

    def configure(self, bar_min: int) -> None:
        """데이터 봉 간격(분) 주입 — run.py가 무인자 생성 후 호출. 봉키 정규화 기준."""
        self.bar_sec = float(bar_min) * 60.0

    @property
    def warmup_bars(self) -> int:
        return self.lookback + 1                    # walk-forward priming 길이 계약

    @abstractmethod
    def rank(self, history: dict, snapshot: dict) -> list:
        """상위→하위로 정렬된 보유 후보 심볼 리스트(상위 top_n 보유). 신호의 유일한 추상 메서드."""

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        k = bar_key(tick.ts, self.bar_sec)
        if self.cur_bar is None:
            self.cur_bar = k
        elif k != self.cur_bar:                     # 새 봉 첫 틱 → 직전 봉 확정·리밸런싱
            self._on_bar_close(broker, tick.ts)
            self.cur_bar = k
        self.snapshot[tick.symbol] = tick.price
        self.history.setdefault(tick.symbol, deque(maxlen=self.warmup_bars)).append(float(tick.price))

    def _on_bar_close(self, broker: Broker, ts: float) -> None:
        """직전 봉(snapshot=그 봉 종가)의 랭킹으로 상위 N 동일가중 재조정. 매도/청산 먼저 → 매수.

        **전 종목을 봉 종가(snapshot)로 일관 체결**한다(틱 도착순 무관·룩어헤드 없음 — 사이징=체결).
        랭킹·매수는 당봉 관측 종목만(미관측 carry-forward 차단). 단 **보유했으나 비선정/미관측 종목은
        long-or-cash 원칙상 전량 청산**(미관측이면 last_price로 매도). 결정 규칙은 공용 decide(저회전 밴드).
        """
        if len(self.snapshot) < 2:                  # 횡단면 불가(단일종목) → 무거래
            self.snapshot.clear()
            return
        ranked = self.rank(self.history, self.snapshot)
        longs = ranked[:self.top_n]
        if not longs:                               # 워밍업 등으로 후보 없음 → 무거래
            self.snapshot.clear()
            return
        long_set = set(longs)
        tw = min(Decimal(1) / Decimal(len(longs)), self.max_weight)   # 동일가중(종목당 상한 cap)
        equity, cash = broker.equity(), broker.cash()
        held = set(broker.iter_positions()) | long_set
        for sym in held:                            # 1) 매도/청산 먼저(현금 확보)
            px = self.snapshot[sym] if sym in self.snapshot else broker.price(sym)  # 관측=봉종가, 미관측 보유=last_price(청산용)
            if px <= 0:
                continue
            target = tw if sym in long_set else Decimal(0)   # 비선정 보유 = 전량청산(long-or-cash)
            order = decide(broker.position_qty(sym), px, cash, equity, float(target), self.band)
            if order and order[0] == "SELL":
                broker.sell(sym, order[1], "XS_REBAL", ts, px)
        equity, cash = broker.equity(), broker.cash()   # 매도 반영 후 갱신(사이징 정확)
        for sym in longs:                           # 2) 매수 (longs ⊆ snapshot 보장)
            px = self.snapshot[sym]
            order = decide(broker.position_qty(sym), px, cash, equity, float(tw), self.band)
            if order and order[0] == "BUY":
                broker.buy(sym, order[1], ts, px)
        self.snapshot.clear()


def _lookback_return(dq: deque, lookback: int) -> float | None:
    """윈도우의 lookback봉 수익률. 표본 부족/기준가≤0이면 None."""
    if len(dq) < lookback + 1:
        return None
    base = dq[-1 - lookback]
    return (dq[-1] / base - 1.0) if base > 0 else None


class XSReversalStrategy(CrossSectionalStrategy):
    """횡단면 단기 리버설 — 패자(최저 lookback 수익률) 매수. research §2.4 롱 다리(리테일 contrarian 압력)."""
    name = "xs_reversal"

    def rank(self, history: dict, snapshot: dict) -> list:
        scores = {s: r for s in history if s in snapshot
                  and (r := _lookback_return(history[s], self.lookback)) is not None}
        return sorted(scores, key=scores.get)            # 오름차순 → 패자가 상위 = 매수


class XSMomentumStrategy(CrossSectionalStrategy):
    """횡단면 모멘텀 — 승자(최고 lookback 수익률) 매수. research §2.4 롱 다리(Blitz et al. 2020)."""
    name = "xs_momentum"

    def rank(self, history: dict, snapshot: dict) -> list:
        scores = {s: r for s in history if s in snapshot
                  and (r := _lookback_return(history[s], self.lookback)) is not None}
        return sorted(scores, key=scores.get, reverse=True)   # 내림차순 → 승자가 상위 = 매수
