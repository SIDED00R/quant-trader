"""온디맨드 1회 매매 잡 (단일 책임: 최신 일봉 목표로 동기 주문·체결 후 종료).

온디맨드 매매 VM이 부팅 시 1회 실행한다. 스트리밍(commander/engine/portfolio 상시) 대신,
candles_1d·포지션을 읽어 부하 합성 목표비중을 산출하고 주문→체결을 **동기**로 처리(Kafka 불요) 후 끝낸다.
일봉 저빈도 매매라 배치가 자연스럽다. 원격 DB는 env(CLICKHOUSE_HOST/POSTGRES_HOST)로 데이터 VM을 가리킨다.

순수 결정부 plan_orders는 테스트 가능. 체결·상태 갱신은 검증된 portfolio.apply_execution을 재사용한다.
재사용: commander.decide·combined_for_bar, live_ensemble.prime, ensemble.default_loads, load_weights, apply_execution.
계정/시세 읽기 헬퍼는 commander와 일시 중복(스트리밍 commander 은퇴 시 정리).
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from common.candles import daily_candles
from common.clickhouse_client import create_client
from common.config import ENSEMBLE_REBALANCE_BAND, ENSEMBLE_SYMBOLS, FEE_RATE
from common.postgres_client import close_pool, open_pool, pool
from common.strategy_weights import load_weights
from portfolio.updater import apply_execution
from strategy.commander import combined_for_bar, decide
from strategy.ensemble import default_loads
from strategy.live_ensemble import LiveEnsemble

_FEE_QUANT = Decimal("0.0001")


# ── 계정/시세 읽기 (commander와 일시 중복 — 스트리밍 commander 은퇴 시 통합) ──
def enabled_accounts():
    with pool.connection() as conn:
        return [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE auto_trade=TRUE").fetchall()]


def positions(acct):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT symbol, quantity FROM positions WHERE account_id=%s AND quantity>0", (acct,)).fetchall()
    return {r[0]: Decimal(str(r[1])) for r in rows}


def cash(acct):
    with pool.connection() as conn:
        row = conn.execute("SELECT krw_balance FROM accounts WHERE account_id=%s", (acct,)).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)


def equity(cash_amt: Decimal, pos: dict, prices: dict) -> Decimal:
    eq = cash_amt
    for sym, qty in pos.items():
        p = prices.get(sym)
        if p:
            eq += qty * p
    return eq


def latest_prices(ch_client) -> dict:
    res = ch_client.query(
        "SELECT symbol, argMax(price, seq) FROM ticks WHERE trade_ts > now() - INTERVAL 1 HOUR GROUP BY symbol")
    return {r[0]: Decimal(str(r[1])) for r in res.result_rows}


def compute_targets() -> dict:
    """최신 완료 일봉으로 각 종목의 부하 합성 목표비중 산출. {symbol: weight}. live_ensemble.prime 재사용."""
    hist: dict = {s: [] for s in ENSEMBLE_SYMBOLS}
    for sym, close, ts in daily_candles(ENSEMBLE_SYMBOLS):
        if sym in hist:
            hist[sym].append((datetime.fromtimestamp(ts, timezone.utc).date(), close))
    primed = LiveEnsemble(ENSEMBLE_SYMBOLS).prime(hist)     # [(symbol, day, [(load, target)...])]
    roster = [n for n, _, _ in default_loads()]
    weights = load_weights(roster)
    targets = {}
    for sym, day, per_load in primed:
        latest = {n: (str(day), float(t)) for n, t in per_load}
        combined = combined_for_bar(latest, roster, str(day), weights)
        if combined is not None:
            targets[sym] = combined
    return targets


def plan_orders(targets: dict, snapshots: dict, band: float) -> list:
    """순수: 목표비중 + 계정 스냅샷 → 실행할 주문 [(acct, symbol, side, qty)]. 규칙=commander.decide.

    snapshots: {acct: {"cash": Decimal, "positions": {sym: Decimal}, "prices": {sym: Decimal}}}.
    배치 내 다종목 일관성을 위해 결정마다 현금·보유를 갱신(순차) — 같은 계정의 뒤 종목이 앞 매수 반영분으로 결정.
    """
    out = []
    for acct, snap in snapshots.items():
        acct_cash = snap["cash"]
        pos = dict(snap["positions"])
        prices = snap["prices"]
        for sym, target_w in targets.items():
            px = prices.get(sym)
            if not px:                       # 최신가 없으면 체결 불가 → 스킵
                continue
            qty = pos.get(sym, Decimal(0))
            order = decide(qty, px, acct_cash, equity(acct_cash, pos, prices), target_w, band)
            if order is None:
                continue
            side, oqty = order
            out.append((acct, sym, side, oqty))
            if side == "BUY":               # 다음 종목 결정에 반영(근사 — 정확한 차감은 apply_execution)
                acct_cash -= oqty * px * (Decimal(1) + FEE_RATE)
                pos[sym] = qty + oqty
            else:
                acct_cash += oqty * px * (Decimal(1) - FEE_RATE)
                pos[sym] = qty - oqty
    return out


def _execute(conn, acct, symbol, side, qty: Decimal, price: Decimal) -> str:
    """주문(PENDING, outbox 없음) INSERT 후 동기 체결(apply_execution: 검증·상태 갱신·FILLED). 반환=결과."""
    order_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO orders (order_id, account_id, symbol, side, type, price, quantity, status) "
        "VALUES (%s,%s,%s,%s,'MARKET',%s,%s,'PENDING')",
        (order_id, acct, symbol, side, price, qty))
    fee = (price * qty * FEE_RATE).quantize(_FEE_QUANT)
    ex = {"execution_id": str(uuid.uuid4()), "order_id": order_id, "account_id": acct,
          "symbol": symbol, "side": side, "price": price, "quantity": qty, "fee": fee}
    return apply_execution(conn, ex)


def run(dry_run: bool = False) -> int:
    """1회 매매 실행. dry_run이면 목표·계획 주문만 출력(주문 미발생). 거부 시 비정상 종료코드(스케줄러 감지)."""
    open_pool()
    ch = create_client()
    rejected = 0
    try:
        prices = latest_prices(ch)
        targets = compute_targets()
        band = float(ENSEMBLE_REBALANCE_BAND)
        accts = enabled_accounts()
        snapshots = {a: {"cash": cash(a), "positions": positions(a), "prices": prices} for a in accts}
        orders = plan_orders(targets, snapshots, band)
        shown = {k: round(v, 4) for k, v in targets.items()}
        tag = " [DRY-RUN]" if dry_run else ""
        print(f"[trade_once]{tag} targets={shown} accounts={len(accts)} orders={len(orders)}")
        if dry_run:
            for acct, sym, side, qty in orders:
                print(f"[trade_once] (dry-run) would {side} {sym} qty={qty} acct={acct} @ {prices[sym]}")
            print("[trade_once] dry-run done")
            return 0
        for acct, sym, side, qty in orders:
            with pool.connection() as conn:
                result = _execute(conn, acct, sym, side, qty, prices[sym])
            if result == "rejected":
                rejected += 1
            print(f"[trade_once] {result} {side} {sym} qty={qty} acct={acct} @ {prices[sym]}")
        if rejected:
            print(f"[trade_once] ⚠ {rejected}/{len(orders)} 주문 거부(잔고/보유 부족) — 확인 필요")
        print("[trade_once] done")
        return 1 if rejected else 0
    finally:
        close_pool()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="온디맨드 1회 매매 (5.5)")
    p.add_argument("--dry-run", action="store_true", help="목표·계획 주문만 출력(주문 미발생)")
    raise SystemExit(run(p.parse_args().dry_run))
