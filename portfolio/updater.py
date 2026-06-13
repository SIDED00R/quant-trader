"""포트폴리오 서비스 (단일 책임: executions → Postgres 잔고/포지션).

executions 토픽을 at-least-once로 소비하고, execution_id 기준으로 멱등 처리한다.
체결 적용 전 매수 잔고/매도 보유 수량을 검증해 음수 잔고·유령 매도를 막고,
불가능하면 주문을 REJECTED 처리한다(잔고/포지션 불변). DB 트랜잭션 커밋 후 오프셋 커밋.
"""
import json

from common.config import TOPIC_EXECUTIONS
from common.kafka_client import create_consumer
from common.postgres_client import close_pool, open_pool, pool

GROUP_ID = "portfolio-updater"


def _record_execution(conn, ex: dict) -> None:
    conn.execute(
        "INSERT INTO executions "
        "(execution_id, order_id, account_id, symbol, side, price, quantity, fee) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (ex["execution_id"], ex["order_id"], ex["account_id"], ex["symbol"],
         ex["side"], ex["price"], ex["quantity"], ex["fee"]),
    )


def _reject(conn, order_id: str) -> str:
    conn.execute(
        "UPDATE orders SET status='REJECTED' WHERE order_id=%s AND status='PENDING'",
        (order_id,),
    )
    return "rejected"


def apply_execution(conn, ex: dict) -> str:
    """반환: 'applied' | 'duplicate' | 'rejected'."""
    eid, oid = ex["execution_id"], ex["order_id"]
    acct, sym, side = ex["account_id"], ex["symbol"], ex["side"]
    price, qty, fee = float(ex["price"]), float(ex["quantity"]), float(ex["fee"])

    if conn.execute("SELECT 1 FROM executions WHERE execution_id=%s", (eid,)).fetchone():
        return "duplicate"

    if side == "BUY":
        cost = price * qty + fee
        bal = conn.execute(
            "SELECT krw_balance FROM accounts WHERE account_id=%s FOR UPDATE", (acct,)
        ).fetchone()
        if bal is None or float(bal[0]) < cost:
            return _reject(conn, oid)
        _record_execution(conn, ex)
        conn.execute(
            "UPDATE accounts SET krw_balance = krw_balance - %s WHERE account_id=%s",
            (cost, acct),
        )
        unit_cost = cost / qty  # 수수료 포함 취득단가 → 평단에 반영
        conn.execute(
            "INSERT INTO positions (account_id, symbol, quantity, avg_buy_price) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (account_id, symbol) DO UPDATE SET "
            "avg_buy_price = (positions.quantity*positions.avg_buy_price "
            "  + EXCLUDED.quantity*EXCLUDED.avg_buy_price) "
            "  / NULLIF(positions.quantity + EXCLUDED.quantity, 0), "
            "quantity = positions.quantity + EXCLUDED.quantity",
            (acct, sym, qty, unit_cost),
        )
    else:  # SELL
        held = conn.execute(
            "SELECT quantity FROM positions WHERE account_id=%s AND symbol=%s FOR UPDATE",
            (acct, sym),
        ).fetchone()
        if held is None or float(held[0]) < qty:
            return _reject(conn, oid)
        _record_execution(conn, ex)
        conn.execute(
            "UPDATE accounts SET krw_balance = krw_balance + %s WHERE account_id=%s",
            (price * qty - fee, acct),
        )
        conn.execute(
            "UPDATE positions SET quantity = quantity - %s WHERE account_id=%s AND symbol=%s",
            (qty, acct, sym),
        )

    conn.execute("UPDATE orders SET status='FILLED' WHERE order_id=%s", (oid,))
    return "applied"


def run() -> None:
    open_pool()
    consumer = create_consumer(GROUP_ID)
    consumer.subscribe([TOPIC_EXECUTIONS])
    print("[portfolio] started")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            ex = json.loads(msg.value())
            with pool.connection() as conn:
                result = apply_execution(conn, ex)
            consumer.commit(msg)
            if result != "duplicate":
                print(f"[portfolio] {result} {ex['side']} {ex['symbol']} "
                      f"qty={ex['quantity']} @ {ex['price']}")
    finally:
        consumer.close()
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[portfolio] stopped")
