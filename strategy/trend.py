"""저회전 추세추종 전략 (단일 책임: 일봉 long-or-cash + 변동성 타게팅 진입 사이징).

상위 타임프레임(`--bar-min 1440`=일봉)에서 단기SMA>장기SMA면 상승추세로 보고 보유, 반전(단기<장기)
또는 극단 변동성 레짐이면 전량 현금화한다(공매도 없음). 진입 후엔 청산 기준(추세 반전) 전까지 보유해
거래·수수료를 구조적으로 줄인다(저회전). 진입 비중은 목표변동성/실현변동성으로 사이징(고변동→소액, 저변동→상한).

다른 후보(rsi/macd/...)와 달리 STOP/TAKE/TRAIL·sma_trader에 의존하지 않는다 — 청산 기준이 추세 반전 자체다.
워밍업 가드는 초가 아닌 **봉 수**로 둔다(타임프레임 비의존). config/base에만 의존(Kafka/DB 비의존).
"""
import math
from collections import deque
from decimal import ROUND_DOWN, Decimal

from common.config import (
    FEE_RATE,
    MIN_ORDER_KRW,
    TREND_BARS_PER_YEAR,
    TREND_ENTRY_BAND,
    TREND_LONG,
    TREND_MAX_WEIGHT,
    TREND_REGIME_MAX_VOL,
    TREND_SHORT,
    TREND_VOL_LOOKBACK,
    TREND_VOL_TARGET,
)
from strategy.base import Broker, MarketTick, Strategy

_FEE_QUANT = Decimal("0.0001")  # 체결 수수료 양자화 단위(fills.QUANT_FEE와 동일) — 사이징 시 올림 여유분 예약


def _sma(closes: list[float], n: int) -> float:
    return sum(closes[-n:]) / n


def _ann_vol(closes: list[float], lookback: int, bars_per_year: int):
    """최근 lookback개 로그수익률 표준편차를 연율화(√bars_per_year). 데이터 부족이면 None."""
    if len(closes) < lookback + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - lookback, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(bars_per_year)


class TrendStrategy(Strategy):
    name = "trend"

    def __init__(self, short=None, long=None, entry_band=None, vol_target=None,
                 vol_lookback=None, max_weight=None, regime_max_vol=None, bars_per_year=None):
        # 파라미터는 config 기본값. walk-forward 그리드탐색은 인자로 오버라이드해 인스턴스화한다.
        self.short = short or TREND_SHORT
        self.long = long or TREND_LONG
        if self.short >= self.long:   # 단기≥장기면 추세 정의가 성립 안 함 — 조용한 오계산 대신 조기 거부
            raise ValueError(f"short({self.short}) must be < long({self.long})")
        self.entry_band = float(TREND_ENTRY_BAND if entry_band is None else entry_band)
        self.vol_target = float(TREND_VOL_TARGET if vol_target is None else vol_target)
        self.vol_lookback = vol_lookback or TREND_VOL_LOOKBACK
        self.max_weight = Decimal(str(TREND_MAX_WEIGHT if max_weight is None else max_weight))
        self.regime_max_vol = float(TREND_REGIME_MAX_VOL if regime_max_vol is None else regime_max_vol)
        self.bars_per_year = bars_per_year or TREND_BARS_PER_YEAR
        # 지표 충족 최소 봉 수(=walk-forward priming 길이). short도 포함해 _sma 슬라이스가 항상 충분하도록.
        self.warmup_bars = max(self.short, self.long, self.vol_lookback + 1)
        self.prices: dict[str, deque] = {}

    def on_tick(self, tick: MarketTick, broker: Broker) -> None:
        sym, price, now = tick.symbol, tick.price, tick.ts
        dq = self.prices.setdefault(sym, deque(maxlen=self.warmup_bars + 1))
        dq.append(float(price))
        if len(dq) < self.warmup_bars:      # 워밍업(지표 미충족) — walk-forward에선 OOS 직전 priming 구간
            return
        closes = list(dq)
        sma_s = _sma(closes, self.short)
        sma_l = _sma(closes, self.long)
        ann_vol = _ann_vol(closes, self.vol_lookback, self.bars_per_year)
        extreme = ann_vol is not None and ann_vol > self.regime_max_vol
        trend_up = sma_s > sma_l * (1 + self.entry_band)
        trend_down = sma_s < sma_l * (1 - self.entry_band)   # 히스테리시스: 진입/청산 사이 중립대 → whipsaw 차단

        if broker.position_qty(sym) > 0:
            if extreme or trend_down:       # 청산: 추세 반전 또는 극단 레짐 → 전량 현금
                broker.sell(sym, broker.position_qty(sym), "SIGNAL", now)
            return                           # 추세 유지 중엔 보유(저회전 — 매 봉 리밸런스 안 함)
        if trend_up and not extreme:        # 진입: 변동성 타게팅 비중 1회 산정
            self._enter(sym, price, now, ann_vol, broker)

    def _enter(self, sym, price, now, ann_vol, broker):
        if price is None or price <= 0:
            return
        if not ann_vol or ann_vol <= 0:     # 무변동 → 상한 비중
            weight = self.max_weight
        else:
            weight = min(self.max_weight, Decimal(str(self.vol_target)) / Decimal(str(ann_vol)))
        budget = min(broker.equity() * weight, broker.cash())  # 총자산 기준 목표금액, 단 현금 한도 내
        if budget < MIN_ORDER_KRW:
            return
        # 수수료 포함 총비용이 예산을 넘지 않게 (1+수수료)로 나누고 내림. 추가로 수수료 양자화(HALF_EVEN 올림,
        # 최대 _FEE_QUANT/2)분을 예약해 budget==cash(전액 진입) 시 체결가 반올림으로 잔고 거부되는 경우를 차단.
        qty = ((budget - _FEE_QUANT) / (price * (1 + FEE_RATE))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        if qty > 0:
            broker.buy(sym, qty, now)   # 거부 시(슬리피지 등 잔고 부족) 다음 봉에 재시도 — 추세 유지 중 미보유면 재진입
