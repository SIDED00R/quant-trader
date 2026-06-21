"""온디맨드 1회 매매 잡 (단일 책임: 최신 일봉 목표로 동기 주문·체결 후 종료).

온디맨드 매매 VM이 부팅 시 1회 실행한다. 스트리밍(commander/engine/portfolio 상시) 대신,
candles_1d·포지션을 읽어 부하 합성 목표비중을 산출하고 주문→체결을 **동기**로 처리(Kafka 불요) 후 끝낸다.
일봉 저빈도 매매라 배치가 자연스럽다. 원격 DB는 env(CLICKHOUSE_HOST/POSTGRES_HOST)로 데이터 VM을 가리킨다.

순수 결정부 plan_decisions는 테스트 가능(매매·유지 전부를 사유와 함께 산출). 체결·상태 갱신은 검증된
portfolio.apply_execution을 재사용한다. 매 실행의 결정은 trade_decisions에 기록(대시보드 '매매 결정 기록' 탭).
재사용: commander.decide·combined_for_bar, decision_record.classify, live_ensemble.prime/signals_for,
ensemble.default_loads, load_weights, apply_execution. 계정/시세 읽기 헬퍼는 commander와 일시 중복(스트리밍 commander 은퇴 시 정리).
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from psycopg.types.json import Jsonb

from common.candles import daily_candles
from common.clickhouse_client import create_client
from common.config import ENSEMBLE_REBALANCE_BAND, ENSEMBLE_SYMBOLS, FEE_RATE
from common.postgres_client import close_pool, open_pool, pool
from common.strategy_weights import load_weights
from portfolio.updater import apply_execution
from strategy.commander import combined_for_bar, decide
from strategy.decision_record import classify
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
    """최신 완료 일봉으로 종목별 합성 목표비중 + 부하별 근거 산출. live_ensemble.prime/signals_for 재사용.

    반환 {symbol: {"target": float|None, "bar_date": date|None, "signals": [...]}}.
    target=None(=일부 부하가 다른 봉 → 합성 불가) 종목·이력 없는 종목도 키로 포함(미매매 결정 기록용).
    """
    hist: dict = {s: [] for s in ENSEMBLE_SYMBOLS}
    for sym, close, ts in daily_candles(ENSEMBLE_SYMBOLS):
        if sym in hist:
            hist[sym].append((datetime.fromtimestamp(ts, timezone.utc).date(), close))
    ens = LiveEnsemble(ENSEMBLE_SYMBOLS)
    primed = ens.prime(hist)     # [(symbol, day, [(load, target)...])]
    roster = [n for n, _, _ in default_loads()]
    weights = load_weights(roster)
    out = {s: {"target": None, "bar_date": None, "signals": []} for s in ENSEMBLE_SYMBOLS}
    for sym, day, per_load in primed:
        latest = {n: (str(day), float(t)) for n, t in per_load}
        out[sym] = {
            "target": combined_for_bar(latest, roster, str(day), weights),
            "bar_date": day,
            "signals": ens.signals_for(sym),
        }
    return out


def _decision(acct, sym, price, target_w, eq, action, oqty, reason, analysis) -> dict:
    """결정 1건 레코드(기록·실행 공용). amount=수량×시세(매매 시)."""
    return {
        "account_id": acct, "symbol": sym, "price": price, "target": target_w,
        "equity": eq, "action": action, "quantity": oqty,
        "amount": (oqty * price) if (oqty is not None and price is not None) else None,
        "reason": reason, "bar_date": analysis["bar_date"], "signals": analysis["signals"],
    }


def plan_decisions(analysis: dict, snapshots: dict, band: float) -> list:
    """순수: 종목별 분석 + 계정 스냅샷 → 결정 레코드 리스트(매매·유지 전부, 사유 포함). 규칙=commander.decide.

    analysis: {symbol: {"target": float|None, "bar_date": date|None, "signals": [...]}}.
    snapshots: {acct: {"cash": Decimal, "positions": {sym: Decimal}, "prices": {sym: Decimal}}}.
    배치 내 다종목 일관성을 위해 매매 결정마다 현금·보유를 순차 갱신 — 같은 계정의 뒤 종목이 앞 매수 반영분으로 결정.
    """
    out = []
    for acct, snap in snapshots.items():
        acct_cash = snap["cash"]
        pos = dict(snap["positions"])
        prices = snap["prices"]
        for sym, a in analysis.items():
            target_w = a["target"]
            px = prices.get(sym)
            qty = pos.get(sym, Decimal(0))
            if not px:                       # 최신가 없으면 체결 불가 → 유지(사유 기록)
                out.append(_decision(acct, sym, None, target_w, None, "HOLD", None,
                                     "최신가 없음 — 체결 불가", a))
                continue
            eq = equity(acct_cash, pos, prices)
            order = None if target_w is None else decide(qty, px, acct_cash, eq, target_w, band)
            action, reason = classify(order, target_w, qty)
            oqty = order[1] if order is not None else None
            out.append(_decision(acct, sym, px, target_w, eq, action, oqty, reason, a))
            if action == "BUY":              # 다음 종목 결정에 반영(근사 — 정확한 차감은 apply_execution)
                acct_cash -= oqty * px * (Decimal(1) + FEE_RATE)
                pos[sym] = qty + oqty
            elif action == "SELL":
                acct_cash += oqty * px * (Decimal(1) - FEE_RATE)
                pos[sym] = qty - oqty
    return out


def plan_orders(targets: dict, snapshots: dict, band: float) -> list:
    """{sym: target} → 실행 주문 [(acct, symbol, side, qty)] — plan_decisions의 매매분만 추린 뷰(회귀 테스트용)."""
    analysis = {s: {"target": t, "bar_date": None, "signals": []} for s, t in targets.items()}
    return [(d["account_id"], d["symbol"], d["action"], d["quantity"])
            for d in plan_decisions(analysis, snapshots, band) if d["action"] in ("BUY", "SELL")]


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


def _record_decision(conn, run_ts, d: dict, executed: bool) -> None:
    """결정 1건(매매·유지 전부)을 trade_decisions에 기록. signals는 JSONB(부하별 근거)."""
    conn.execute(
        "INSERT INTO trade_decisions (decision_id, run_ts, bar_date, account_id, symbol, price, "
        "target_weight, action, quantity, amount_krw, equity, reason, signals, executed) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (str(uuid.uuid4()), run_ts, d["bar_date"], d["account_id"], d["symbol"], d["price"],
         d["target"], d["action"], d["quantity"], d["amount"], d["equity"], d["reason"],
         Jsonb(d["signals"]), executed))


def run(dry_run: bool = False) -> int:
    """1회 매매 실행. dry_run이면 목표·계획만 출력(주문·기록 미발생). 거부 시 비정상 종료코드(스케줄러 감지)."""
    open_pool()
    ch = create_client()
    rejected = 0
    try:
        prices = latest_prices(ch)
        analysis = compute_targets()
        band = float(ENSEMBLE_REBALANCE_BAND)
        accts = enabled_accounts()
        snapshots = {a: {"cash": cash(a), "positions": positions(a), "prices": prices} for a in accts}
        decisions = plan_decisions(analysis, snapshots, band)
        run_ts = datetime.now(timezone.utc)
        trades = [d for d in decisions if d["action"] in ("BUY", "SELL")]
        shown = {s: round(a["target"], 4) for s, a in analysis.items() if a["target"] is not None}
        tag = " [DRY-RUN]" if dry_run else ""
        print(f"[trade_once]{tag} targets={shown} accounts={len(accts)} orders={len(trades)}")
        if dry_run:
            for d in trades:
                print(f"[trade_once] (dry-run) would {d['action']} {d['symbol']} qty={d['quantity']} acct={d['account_id']} @ {d['price']}")
            print("[trade_once] dry-run done")
            return 0
        for d in decisions:
            with pool.connection() as conn:    # 체결+기록을 한 트랜잭션에 묶어 '체결됐는데 기록 없음' 갭 제거
                executed = False
                if d["action"] in ("BUY", "SELL"):
                    result = _execute(conn, d["account_id"], d["symbol"], d["action"], d["quantity"], d["price"])
                    executed = result == "applied"
                    if result == "rejected":
                        rejected += 1
                    print(f"[trade_once] {result} {d['action']} {d['symbol']} qty={d['quantity']} acct={d['account_id']} @ {d['price']}")
                _record_decision(conn, run_ts, d, executed)
        if rejected:
            print(f"[trade_once] ⚠ {rejected}/{len(trades)} 주문 거부(잔고/보유 부족) — 확인 필요")
        print(f"[trade_once] done — decisions={len(decisions)} recorded")
        return 1 if rejected else 0
    finally:
        close_pool()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="온디맨드 1회 매매 (5.5)")
    p.add_argument("--dry-run", action="store_true", help="목표·계획 주문만 출력(주문 미발생)")
    raise SystemExit(run(p.parse_args().dry_run))
