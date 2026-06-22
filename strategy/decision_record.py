"""매매결정 분류 (단일 책임: 재조정 결정과 그 사유·수치를 순수 계산).

`decide()`(commander)가 (side, qty)|None 만 돌려주던 규칙을, 매매 여부와 무관하게
**모든 종목에 대해** 결정(BUY/SELL/HOLD/SKIP)·사유·수치(목표/현재 비중·격차)로 풀어 반환한다.
commander.decide 와 trade_once 가 이 한 함수를 공유한다(규칙 단일 출처 — decide 는 이걸 BUY/SELL 만 추림).
순수 함수(가격·현금·평가액만 의존, DB/IO 없음) — 테스트 가능.
"""
from decimal import ROUND_DOWN, Decimal
from typing import NamedTuple, Optional

from common.config import FEE_RATE, MIN_ORDER_KRW

_FEE_QUANT = Decimal("0.0001")
_QTY_QUANT = Decimal("0.00000001")


class Decision(NamedTuple):
    decision: str                  # BUY | SELL | HOLD | SKIP
    quantity: Decimal              # BUY/SELL 수량, 그 외 0
    target_w: float                # 목표비중
    current_w: Optional[float]     # 현재비중(가격 미확보 SKIP 시 None=미산출)
    gap: Optional[float]           # 목표 − 현재 (%p 의미; 미산출 시 None)
    reason: str                    # 사람이 읽는 사유(수치 포함)


def classify(qty: Decimal, price: Optional[Decimal], cash: Decimal, equity: Decimal,
             target_w: float, band: float) -> Decision:
    """보유 수량을 목표비중으로 재조정하는 결정 + 사유. decide()와 동일한 임계(밴드·최소주문·현금한도)."""
    if not price:                                       # 최신가 미확보 → 체결·평가 불가(현재비중 미산출)
        return Decision("SKIP", Decimal(0), target_w, None, None,
                        "최신가 미확보(최근 1시간 틱 없음) → 스킵")
    if price <= 0 or equity <= 0:
        return Decision("SKIP", Decimal(0), target_w, None, None,
                        "가격/평가액 0 이하 → 스킵")

    cur_val = qty * price
    current_w = float(cur_val / equity)
    gap = target_w - current_w
    target_val = equity * Decimal(str(target_w))

    if target_w <= 0:
        if qty > 0:
            return Decision("SELL", qty, target_w, current_w, gap, "목표비중 0 → 전량 매도")
        return Decision("HOLD", Decimal(0), target_w, current_w, gap, "목표비중 0·보유 없음 → 유지")

    band_w = band * target_w
    if qty > 0 and abs(cur_val - target_val) / equity < Decimal(str(band)) * Decimal(str(target_w)):
        return Decision("HOLD", Decimal(0), target_w, current_w, gap,
                        f"격차 {gap:+.1%} ≤ 밴드 {band_w:.1%} → 유지")

    if target_val > cur_val:                            # 확대 → 차액 매수(현금 한도 내)
        budget = min(target_val - cur_val, cash)
        if budget < MIN_ORDER_KRW:
            return Decision("HOLD", Decimal(0), target_w, current_w, gap,
                            f"매수 예정액 {budget:,.0f}원 < 최소주문 {MIN_ORDER_KRW:,.0f}원 → 유지")
        qbuy = ((budget - _FEE_QUANT) / (price * (1 + FEE_RATE))).quantize(_QTY_QUANT, rounding=ROUND_DOWN)
        if qbuy > 0:
            return Decision("BUY", qbuy, target_w, current_w, gap,
                            f"격차 {gap:+.1%} > 밴드 {band_w:.1%} → {qbuy} 매수")
        return Decision("HOLD", Decimal(0), target_w, current_w, gap, "매수 수량 0 → 유지")

    sell_val = cur_val - target_val                     # 축소 → 차액 매도(최소주문 이상만)
    if sell_val < MIN_ORDER_KRW:
        return Decision("HOLD", Decimal(0), target_w, current_w, gap,
                        f"매도 예정액 {sell_val:,.0f}원 < 최소주문 {MIN_ORDER_KRW:,.0f}원 → 유지")
    qsell = min((sell_val / price).quantize(_QTY_QUANT, rounding=ROUND_DOWN), qty)
    if qsell > 0:
        return Decision("SELL", qsell, target_w, current_w, gap,
                        f"격차 {gap:+.1%} (|격차| > 밴드 {band_w:.1%}) → {qsell} 매도")
    return Decision("HOLD", Decimal(0), target_w, current_w, gap, "매도 수량 0 → 유지")
