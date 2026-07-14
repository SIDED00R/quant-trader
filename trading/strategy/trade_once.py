"""온디맨드 1회 매매 잡 (단일 책임: 최신 일봉 목표로 동기 주문·체결 후 종료).

온디맨드 매매 VM이 부팅 시 1회 실행한다. 스트리밍(commander/engine/portfolio 상시) 대신,
candles_1d·포지션을 읽어 부하 합성 목표비중을 산출하고 주문→체결을 **동기**로 처리(Kafka 불요) 후 끝낸다.
일봉 저빈도 매매라 배치가 자연스럽다. DB는 매매 VM 로컬(env CLICKHOUSE_HOST/POSTGRES_HOST 기본 127.0.0.1) — startup이 선기동.

순수 결정부 plan_decisions는 테스트 가능(매매·유지 전부를 사유와 함께 산출). 체결·상태 갱신은 검증된
portfolio.apply_execution을 재사용한다. 매 실행의 결정은 trade_decisions에 기록(대시보드 '매매 결정 기록' 탭).
재사용: commander.decide·combined_for_bar, decision_record.classify, live_ensemble.prime/signals_for,
ensemble.default_loads, load_weights, apply_execution. 계정/시세 읽기 헬퍼는 commander와 일시 중복(스트리밍 commander 은퇴 시 정리).
"""
import traceback
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from psycopg.types.json import Jsonb

from common import notify_telegram
from common.candles import daily_candles
from common.config import ENSEMBLE_REBALANCE_BAND, ENSEMBLE_SYMBOLS, FEE_RATE
from common.equity_chart_telegram import send_chart
from common.equity_snapshot import record_snapshot
from common.postgres_client import close_pool, open_pool, pool
from common.strategy_weights import load_weights
from common.upbit_ticker import latest_prices
from trading.portfolio.updater import apply_execution
from trading.strategy.commander import combined_for_bar, decide
from trading.strategy.decision_record import classify
from trading.strategy.ensemble import default_loads
from trading.strategy.live_ensemble import LiveEnsemble
from trading.strategy.notify_messages import coin_message, error_message

_FEE_QUANT = Decimal("0.0001")
_MAX_STALE_DAYS = 3   # 최신 완료 일봉 허용 지연(일). 코인은 무휴장이라 3일이면 백필 고장 확정.


def stale_bar_reason(analysis: dict, today) -> str | None:
    """순수: 일봉이 없거나 _MAX_STALE_DAYS보다 낡았으면 사유 문자열, 신선하면 None.

    startup의 backfill_daily 실패는 `|| true`로 잡을 죽이지 않으므로(1회 실패 + 신선한 기존
    데이터 = 매매 지속), 실제로 낡은 데이터로 목표를 산출하는 순간만 여기서 크게 차단한다
    (주식 경로 stock_trade_common의 MAX_STALE_DAYS 게이트와 동일 취지 — 위반 시 raise→텔레그램).
    """
    dates = [a["bar_date"] for a in analysis.values() if a.get("bar_date")]
    if not dates:
        return "코인 일봉 이력 없음 — candles_1d 백필(backfill_daily) 확인"
    lag = (today - max(dates)).days
    if lag > _MAX_STALE_DAYS:
        return f"코인 일봉 신선도 위반 — 최신봉 {max(dates)}({lag}일 경과 > {_MAX_STALE_DAYS}일). 백필 확인"
    return None


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


def _already_ran_today(conn) -> bool:
    """오늘(UTC) 이미 매매 결정이 기록됐는가 — 코인은 주간 마커가 없어 스위퍼(메인+1h)가 매일 이중 실행되므로
    메인 성공 후 재기록·재알림을 막는다(#218 C2). 메인 실패(기록 0)면 스위퍼가 정상 재시도한다.
    부분 실패(일부 기록 후 예외)면 스위퍼는 스킵 → 나머지는 익일 재시도(코인 밴드 일배치라 ≤1일 지연 수용).
    run_ts는 timestamptz — 세션 TZ와 무관하게 UTC 벽시계로 비교한다(세션이 UTC가 아니어도 정확)."""
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM trade_decisions "
        "WHERE (run_ts AT TIME ZONE 'UTC') >= date_trunc('day', now() AT TIME ZONE 'UTC'))").fetchone()
    return bool(row and row[0])


def run(dry_run: bool = False) -> int:
    """1회 매매 실행. dry_run이면 목표·계획만 출력(주문·기록 미발생).

    종료코드: 0=정상 / 70=거부 발생 또는 오류(텔레그램 통보 완료) / 1=오류인데 통보도 실패(startup 폴백이 발송).
    """
    open_pool()
    if not dry_run:                              # 코인 스위퍼 이중 실행 방지(#218 C2) — 오늘 이미 기록됐으면 스킵
        with pool.connection() as conn:
            already = _already_ran_today(conn)   # 연결은 블록 안에서만 사용 후 반납
        if already:
            print("[trade_once] 오늘 이미 실행됨 — 스위퍼 이중 실행 스킵(주문·기록·알림 없음)")
            close_pool()
            return 0
    rejected = 0
    try:
        prices = latest_prices(ENSEMBLE_SYMBOLS)   # 업비트 REST 현재가(틱 DB 비의존 — 수집 VM과 디커플링)
        analysis = compute_targets()
        stale = stale_bar_reason(analysis, datetime.now(timezone.utc).date())
        if stale:                                  # 낡은 일봉으로 사이징 금지 — raise→텔레그램(exit 70)
            raise RuntimeError(stale)
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
            notify_telegram.send(coin_message(decisions, 0, shown, dry_run=True))
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
                d["executed"] = executed          # 알림 문안용 체결 여부 주석
                _record_decision(conn, run_ts, d, executed)
        if rejected:
            print(f"[trade_once] ⚠ {rejected}/{len(trades)} 주문 거부(잔고/보유 부족) — 확인 필요")
        print(f"[trade_once] done — decisions={len(decisions)} recorded")
        balances = {}
        for a in accts:
            c = cash(a)
            balances[a] = {"cash": c, "equity": equity(c, positions(a), prices)}
        for a, b in balances.items():   # 자산 곡선 원천(equity_snapshots) — 실패는 내부 흡수, 매매 결과 무관
            record_snapshot("COIN", a, "KRW", b["equity"], cash=b["cash"],
                            positions_value=b["equity"] - b["cash"])
        notify_telegram.send(coin_message(decisions, rejected, shown, dry_run=False, balances=balances))
        send_chart()   # 자산 차트 사진 1장/일 — 스위퍼 재실행은 상단 '이미 실행됨' 가드가 차단(비치명)
        return 70 if rejected else 0
    except Exception as e:
        traceback.print_exc()
        sent = notify_telegram.send(error_message("코인", e))
        return 70 if sent else 1
    finally:
        close_pool()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="온디맨드 1회 매매 (5.5)")
    p.add_argument("--dry-run", action="store_true", help="목표·계획 주문만 출력(주문 미발생)")
    raise SystemExit(run(p.parse_args().dry_run))
