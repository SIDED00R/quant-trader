"""추세 신호 코어 (단일 책임: 가격열 → 변동성 타게팅 목표비중, 히스테리시스 래치).

TrendStrategy의 '매매 실행'과 분리한 순수 결정 단위 — 앙상블(EnsembleStrategy)이 여러 속도의 신호를
조합할 때 재사용한다. update(symbol, price)는 내부 가격버퍼와 long 래치를 갱신하고 목표비중(0=현금)을 반환한다.
래치(직전 long/cash 상태)로 히스테리시스를 구현해 진입/청산 사이 중립대에서 상태를 유지한다.
지표 수학은 strategy.trend의 순수 함수(_sma/_ann_vol/target_weight)를 공유한다.
"""
from collections import deque
from decimal import Decimal

from common.config import (
    TREND_BARS_PER_YEAR,
    TREND_ENTRY_BAND,
    TREND_MAX_WEIGHT,
    TREND_REGIME_MAX_VOL,
    TREND_VOL_LOOKBACK,
    TREND_VOL_TARGET,
)
from trading.strategy.plugins.trend import _ann_vol, _sma, target_weight


class TrendSignal:
    def __init__(self, short, long, entry_band=None, vol_target=None, vol_lookback=None,
                 max_weight=None, regime_max_vol=None, bars_per_year=None):
        if short >= long:
            raise ValueError(f"short({short}) must be < long({long})")
        self.short, self.long = short, long
        self.entry_band = float(TREND_ENTRY_BAND if entry_band is None else entry_band)
        self.vol_target = float(TREND_VOL_TARGET if vol_target is None else vol_target)
        self.vol_lookback = vol_lookback or TREND_VOL_LOOKBACK
        self.max_weight = Decimal(str(TREND_MAX_WEIGHT if max_weight is None else max_weight))
        self.regime_max_vol = float(TREND_REGIME_MAX_VOL if regime_max_vol is None else regime_max_vol)
        self.bars_per_year = bars_per_year or TREND_BARS_PER_YEAR
        self.warmup_bars = max(self.short, self.long, self.vol_lookback + 1)
        self.prices: dict[str, deque] = {}
        self.long_state: dict[str, bool] = {}   # 직전 long(보유의향)/cash 래치(히스테리시스)
        self.last: dict[str, dict] = {}         # 종목별 마지막 진단값(결정 근거 기록용 — 반환값과 무관)

    def update(self, symbol: str, price) -> Decimal:
        """가격버퍼·래치 갱신 후 목표비중 반환(0=현금/워밍업). 포지션 보유는 호출자(앙상블)가 관리."""
        dq = self.prices.setdefault(symbol, deque(maxlen=self.warmup_bars + 1))
        dq.append(float(price))
        if len(dq) < self.warmup_bars:
            self.last[symbol] = {"sma_s": None, "sma_l": None, "ann_vol": None,
                                 "long": False, "target": Decimal(0)}
            return Decimal(0)
        closes = list(dq)
        sma_s = _sma(closes, self.short)
        sma_l = _sma(closes, self.long)
        ann_vol = _ann_vol(closes, self.vol_lookback, self.bars_per_year)
        extreme = ann_vol is not None and ann_vol > self.regime_max_vol
        trend_up = sma_s > sma_l * (1 + self.entry_band)
        trend_down = sma_s < sma_l * (1 - self.entry_band)
        long = self.long_state.get(symbol, False)
        if long and (extreme or trend_down):          # 보유의향 → 청산(반전/극단 레짐)
            long = False
        elif (not long) and trend_up and not extreme:  # 현금 → 진입(상승추세)
            long = True
        self.long_state[symbol] = long
        tw = target_weight(ann_vol, self.vol_target, self.max_weight) if long else Decimal(0)
        self.last[symbol] = {"sma_s": sma_s, "sma_l": sma_l, "ann_vol": ann_vol,
                             "long": long, "target": tw}
        return tw
