"""앙상블 commander (단일 책임: strategy.signals 소비 → 목표비중으로 모의주문).

신호 워커(live_ensemble)가 발행한 일봉 목표비중 신호를 받아, auto_trade=TRUE 계정의 보유 비중을
목표로 재조정한다(밴드 초과 시만). 주문은 place_order(내부 orders→relay→엔진 시뮬레이션) — **실거래 아님(모의)**.
종목당 미체결(PENDING) 주문이 있으면 보류(중복 방지). 신호는 일봉당 1회라 저빈도 동작.

decide()는 백테스트 EnsembleStrategy._order_to_target과 동일한 재조정 규칙의 순수 함수(테스트 가능).
run()이 Kafka/Postgres/ClickHouse I/O를 얇게 감싼다. latest offset 구독(기동 시 과거 신호 재실행 안 함).
"""
import json
from decimal import ROUND_DOWN, Decimal

from common.clickhouse_client import create_client
from common.config import ENSEMBLE_REBALANCE_BAND, FEE_RATE, MIN_ORDER_KRW, TOPIC_SIGNALS
from common.kafka_client import create_consumer
from common.order_writer import place_order
from common.postgres_client import close_pool, open_pool, pool

GROUP_ID = "ensemble-commander"
_FEE_QUANT = Decimal("0.0001")


def decide(qty: Decimal, price: Decimal, cash: Decimal, equity: Decimal,
           target_w: float, band: float):
    """보유 수량을 목표비중으로 재조정하는 주문 결정. (side, quantity) 또는 None(유지).

    target_w<=0 → 전량 매도. 밴드 이내 드리프트 → 유지. 확대=차액 매수(수수료 여유분 예약), 축소=차액 매도.
    최소주문(MIN_ORDER_KRW) 미달 거래는 생략(churn 차단). 백테스트 _order_to_target과 동일 규칙.
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


def _enabled_accounts():
    with pool.connection() as conn:
        return [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE auto_trade=TRUE").fetchall()]


def _positions(acct):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT symbol, quantity, avg_buy_price FROM positions WHERE account_id=%s AND quantity>0",
            (acct,)).fetchall()
    return {r[0]: Decimal(str(r[1])) for r in rows}


def _cash(acct):
    with pool.connection() as conn:
        row = conn.execute("SELECT krw_balance FROM accounts WHERE account_id=%s", (acct,)).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)


def _has_pending(acct, symbol):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT 1 FROM orders WHERE account_id=%s AND symbol=%s AND status='PENDING' LIMIT 1",
            (acct, symbol)).fetchone() is not None


def _latest_prices(client):
    res = client.query(
        "SELECT symbol, argMax(price, seq) FROM ticks WHERE trade_ts > now() - INTERVAL 1 HOUR GROUP BY symbol")
    return {r[0]: Decimal(str(r[1])) for r in res.result_rows}


def _equity(cash: Decimal, pos: dict, prices: dict) -> Decimal:
    eq = cash
    for sym, qty in pos.items():
        p = prices.get(sym)
        if p:
            eq += qty * p
    return eq


def _rebalance(acct, symbol, target_w, prices, band):
    if _has_pending(acct, symbol):       # 직전 주문 체결 대기 → 중복 주문 방지
        return
    price = prices.get(symbol)
    if not price:
        return
    pos = _positions(acct)
    qty = pos.get(symbol, Decimal(0))
    order = decide(qty, price, _cash(acct), _equity(_cash(acct), pos, prices), target_w, band)
    if order is None:
        return
    side, quantity = order
    place_order(acct, symbol, side, "MARKET", quantity)
    print(f"[commander] {side} {symbol} qty={quantity} acct={acct} (target={target_w:.3f})")


def run() -> None:
    open_pool()
    client = create_client()
    consumer = create_consumer(GROUP_ID, enable_auto_commit=True, auto_offset_reset="latest")
    consumer.subscribe([TOPIC_SIGNALS])
    band = float(ENSEMBLE_REBALANCE_BAND)
    print(f"[commander] started — {TOPIC_SIGNALS} → place_order (모의), band={band}")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            try:
                sig = json.loads(msg.value())
                symbol, target_w = sig["symbol"], float(sig["target_weight"])
            except (KeyError, ValueError, TypeError) as e:
                print(f"[commander] skip bad signal: {e}")
                continue
            prices = _latest_prices(client)
            for acct in _enabled_accounts():
                _rebalance(acct, symbol, target_w, prices, band)
    finally:
        consumer.close()
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[commander] stopped")
