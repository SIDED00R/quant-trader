"""다부하 Commander (단일 책임: strategy.signals 소비 → 부하 가중합 목표로 모의주문).

각 추세 부하(live_ensemble)가 발행한 일봉 목표비중을 종목별로 모아, **모든 부하가 같은 봉으로 신호한 뒤**
가중치(strategy_weights, 적응 off면 동일가중)로 합성한 목표비중으로 auto_trade 계정을 재조정한다(밴드 초과 시만).
**한 봉당 1회만** 주문(부분 신호로 조기 발주 방지 — 미체결 가드와의 충돌 회피). 주문=place_order(내부 시뮬, 모의).

decide()=백테스트 _order_to_target과 동일 규칙, combined_for_bar()=가중합(둘 다 순수 함수, 테스트 가능).
동일가중이면 합성목표=부하 목표의 평균 = 기존 EnsembleStrategy와 동일(현 동작 보존). latest offset 구독.
"""
import json
from collections import defaultdict
from decimal import Decimal

from common.clickhouse_client import create_client
from common.config import ENSEMBLE_REBALANCE_BAND, TOPIC_SIGNALS
from common.kafka_client import create_consumer
from common.order_writer import place_order
from common.postgres_client import close_pool, open_pool, pool
from common.strategy_weights import load_weights
from trading.strategy.ensemble import default_loads
# 리밸런싱 규칙은 공용 정본(rebalance.py)에서 — 백테스트 횡단면과 동일 규칙·테스트 가능. 재export로 기존 import 호환.
from trading.strategy.rebalance import combined_for_bar, decide, _roster_ready

GROUP_ID = "ensemble-commander"


def _enabled_accounts():
    with pool.connection() as conn:
        return [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE auto_trade=TRUE").fetchall()]


def _positions(acct):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT symbol, quantity FROM positions WHERE account_id=%s AND quantity>0",
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
    roster = [name for name, _, _ in default_loads()]      # 합성에 필요한 부하 전체
    latest: dict = defaultdict(dict)    # symbol → {load: (bar_ts, target)} — 부하별 최신 신호 버퍼
    last_acted: dict = {}               # symbol → bar_ts — 이미 합성·발주한 봉(중복 방지)
    print(f"[commander] started — {TOPIC_SIGNALS} → place_order (모의), roster={roster}, band={band}")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            try:
                sig = json.loads(msg.value())
                symbol, strategy = sig["symbol"], sig["strategy"]
                bar_ts, target = str(sig["bar_ts"]), float(sig["target_weight"])
            except (KeyError, ValueError, TypeError) as e:
                print(f"[commander] skip bad signal: {e}")
                continue
            if strategy not in roster:           # 로스터 외 부하 → 무시
                continue
            sym_latest = latest[symbol]
            sym_latest[strategy] = (bar_ts, target)
            if last_acted.get(symbol) == bar_ts or not _roster_ready(sym_latest, roster, bar_ts):
                continue                          # 이미 발주했거나 일부 부하 미보고 → 대기(DB 미조회)
            combined = combined_for_bar(sym_latest, roster, bar_ts, load_weights(roster))
            if combined is None:                  # 가중합 0 등 비정상 → 안전상 대기
                continue
            prices = _latest_prices(client)
            for acct in _enabled_accounts():
                _rebalance(acct, symbol, combined, prices, band)
            last_acted[symbol] = bar_ts
            print(f"[commander] combined {symbol} target={combined:.4f} (bar={bar_ts}, loads={len(roster)})")
    finally:
        consumer.close()
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[commander] stopped")
