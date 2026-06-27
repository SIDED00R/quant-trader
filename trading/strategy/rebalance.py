"""리밸런싱 순수 함수 (단일 책임: 목표비중→주문 결정·합성·봉키. I/O 비의존 — 라이브·백테스트 공유 정본).

commander(라이브)·cross_sectional(백테스트)·ensemble이 같은 long-or-cash 재조정 규칙을 쓰도록 한 곳에 둔다.
DB/Kafka에 의존하지 않아 ClickHouse 없이 단위테스트 가능(commander의 I/O import와 분리되는 게 핵심).
"""
from decimal import ROUND_DOWN, Decimal

from common.config import FEE_RATE, MIN_ORDER_KRW

_FEE_QUANT = Decimal("0.0001")


def bar_key(ts: float, bar_sec: float) -> int:
    """epoch초 ts를 봉 인덱스로 정규화(부동소수 흔들림 차단). 같은 봉의 다종목 틱을 같은 키로 묶는다."""
    return int(ts // bar_sec)


def _roster_ready(sym_latest: dict, roster: list, bar_ts) -> bool:
    """roster의 모든 부하가 같은 bar_ts로 신호했는지(=합성 가능 여부). sym_latest={load:(bar_ts, target)}."""
    return all(sym_latest.get(n, (None,))[0] == bar_ts for n in roster)


def combined_for_bar(latest: dict, roster: list, bar_ts, weights: dict):
    """roster 모든 부하가 같은 bar_ts로 신호했으면 가중평균 목표비중 반환, 불완전/가중합0이면 None.

    latest={load:(bar_ts, target)}, weights={load: w}. 동일가중이면 부하 목표의 평균.
    """
    if not _roster_ready(latest, roster, bar_ts):    # 일부 부하 미보고 → 대기
        return None
    wsum = sum(weights.get(n, 0.0) for n in roster)
    if wsum <= 0:
        return None
    return sum(weights.get(n, 0.0) * latest[n][1] for n in roster) / wsum


def decide(qty: Decimal, price: Decimal, cash: Decimal, equity: Decimal,
           target_w: float, band: float):
    """보유 수량을 목표비중으로 재조정하는 주문 결정. (side, quantity) 또는 None(유지).

    target_w<=0 → 전량 매도. 밴드 이내 드리프트 → 유지. 확대=차액 매수(수수료 여유분 예약), 축소=차액 매도.
    최소주문(MIN_ORDER_KRW) 미달 거래는 생략(churn 차단).
    """
    if price <= 0 or equity <= 0:
        return None
    if target_w <= 0:
        return ("SELL", qty) if qty > 0 else None
    cur_val = qty * price
    target_val = equity * Decimal(str(target_w))
    if qty > 0 and abs(cur_val - target_val) / equity < Decimal(str(band)) * Decimal(str(target_w)):
        return None                                  # 밴드 이내 → 유지(저회전)
    if target_val > cur_val:                          # 확대 → 차액 매수(현금 한도 내)
        budget = min(target_val - cur_val, cash)
        if budget < MIN_ORDER_KRW:
            return None
        qbuy = ((budget - _FEE_QUANT) / (price * (1 + FEE_RATE))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        return ("BUY", qbuy) if qbuy > 0 else None
    sell_val = cur_val - target_val                   # 축소 → 차액 매도(최소주문 이상만)
    if sell_val < MIN_ORDER_KRW:
        return None
    qsell = min((sell_val / price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN), qty)
    return ("SELL", qsell) if qsell > 0 else None
