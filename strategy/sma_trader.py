"""자동매매 봇 (단일 책임: SMA 교차 전략 → 주문).

market.ticks 를 구독해 종목별 단기/장기 이동평균(SMA)을 갱신하고,
교차가 발생할 때만 신호를 낸다:
  · 골든크로스(단기>장기로 전환) → 매수
  · 데드크로스(단기<장기로 전환) → 매도
auto_trade=TRUE 계정에 대해 주문한다(매수=고정 KRW, 매도=보유 전량, 종목당 1포지션, 쿨다운).

견고성:
- 시세는 latest 부터 소비(과거 틱 재생으로 인한 신호 폭주 방지).
- 첫 확정 신호는 진입 기준선으로만 쓰고 매매하지 않는다(교차 발생 시에만 매매).
- 매수 시 잔고가 부족하면 포트폴리오 서비스가 REJECTED 처리(여기서 음수 잔고 불가).
"""
import json
import time
from collections import deque
from decimal import Decimal

from common.config import (
    SMA_LONG,
    SMA_SHORT,
    STRATEGY_COOLDOWN_SEC,
    STRATEGY_ORDER_KRW,
    TOPIC_TICKS,
)
from common.kafka_client import create_consumer
from common.order_writer import place_order
from common.postgres_client import close_pool, open_pool, pool

GROUP_ID = "strategy"


def enabled_accounts() -> list[str]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT account_id FROM accounts WHERE auto_trade=TRUE"
        ).fetchall()
    return [r[0] for r in rows]


def held_quantity(account_id: str, symbol: str) -> Decimal:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT quantity FROM positions WHERE account_id=%s AND symbol=%s",
            (account_id, symbol),
        ).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)


def sma_signal(prices: deque) -> str | None:
    """현재 SMA 상태: 'BUY'(단기>장기) | 'SELL'(단기<장기) | None(데이터 부족/동일)."""
    if len(prices) < SMA_LONG:
        return None
    p = list(prices)
    short = sum(p[-SMA_SHORT:]) / SMA_SHORT
    long_ = sum(p[-SMA_LONG:]) / SMA_LONG
    if short > long_:
        return "BUY"
    if short < long_:
        return "SELL"
    return None


def trade_signal(symbol: str, side: str) -> None:
    """교차 신호를 auto_trade 계정들에 적용."""
    for acct in enabled_accounts():
        if side == "BUY":
            if held_quantity(acct, symbol) > 0:
                continue  # 이미 보유 → 추가 매수 안 함
            qty = (STRATEGY_ORDER_KRW / _last_price[symbol]).quantize(Decimal("0.00000001"))
            if qty <= 0:
                continue
            place_order(acct, symbol, "BUY", "MARKET", qty)
            print(f"[strategy] BUY {symbol} qty={qty} acct={acct}")
        else:  # SELL
            held = held_quantity(acct, symbol)
            if held <= 0:
                continue
            place_order(acct, symbol, "SELL", "MARKET", held)
            print(f"[strategy] SELL {symbol} qty={held} acct={acct}")


_last_price: dict[str, Decimal] = {}


def run() -> None:
    open_pool()
    consumer = create_consumer(GROUP_ID, enable_auto_commit=True, auto_offset_reset="latest")
    consumer.subscribe([TOPIC_TICKS])
    prices: dict[str, deque] = {}
    state: dict[str, str] = {}        # symbol -> 마지막 확정 신호
    last_trade: dict[str, float] = {}  # symbol -> 마지막 매매 시각(쿨다운)
    print(f"[strategy] started SMA({SMA_SHORT}/{SMA_LONG}) "
          f"order={STRATEGY_ORDER_KRW}KRW cooldown={STRATEGY_COOLDOWN_SEC}s")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            data = json.loads(msg.value())
            symbol = data["symbol"]
            _last_price[symbol] = Decimal(str(data["price"]))
            dq = prices.setdefault(symbol, deque(maxlen=SMA_LONG))
            dq.append(_last_price[symbol])

            sig = sma_signal(dq)
            if sig is None or sig == state.get(symbol):
                continue
            prev = state.get(symbol)
            state[symbol] = sig
            if prev is None:
                continue  # 첫 확정 신호는 기준선으로만
            now = time.monotonic()
            if now - last_trade.get(symbol, 0) < STRATEGY_COOLDOWN_SEC:
                continue
            trade_signal(symbol, sig)
            last_trade[symbol] = now
    finally:
        consumer.close()
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[strategy] stopped")
