"""포트폴리오 서비스 (단일 책임: executions → Postgres 잔고/포지션).

executions 토픽을 at-least-once로 소비하고, execution_id PK로 멱등 처리하여
중복 체결 적용을 막는다. DB 트랜잭션이 끝난 뒤 오프셋을 수동 커밋한다.
"""
import json

from common.config import TOPIC_EXECUTIONS
from common.kafka_client import create_consumer
from common.postgres_client import close_pool, open_pool, pool

GROUP_ID = "portfolio-updater"


def apply_execution(conn, ex: dict) -> bool:
    """멱등 적용. 이미 처리한 execution이면 False."""
    inserted = conn.execute(
        "INSERT INTO executions "
        "(execution_id, order_id, account_id, symbol, side, price, quantity, fee) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (execution_id) DO NOTHING",
        (
            ex["execution_id"], ex["order_id"], ex["account_id"], ex["symbol"],
            ex["side"], ex["price"], ex["quantity"], ex["fee"],
        ),
    )
    if inserted.rowcount == 0:
        return False  # 이미 처리됨

    acct, sym = ex["account_id"], ex["symbol"]
    price, qty, fee = float(ex["price"]), float(ex["quantity"]), float(ex["fee"])

    if ex["side"] == "BUY":
        conn.execute(
            "UPDATE accounts SET krw_balance = krw_balance - %s WHERE account_id=%s",
            (price * qty + fee, acct),
        )
        conn.execute(
            "INSERT INTO positions (account_id, symbol, quantity, avg_buy_price) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (account_id, symbol) DO UPDATE SET "
            "avg_buy_price = (positions.quantity*positions.avg_buy_price "
            "  + EXCLUDED.quantity*EXCLUDED.avg_buy_price) "
            "  / (positions.quantity + EXCLUDED.quantity), "
            "quantity = positions.quantity + EXCLUDED.quantity",
            (acct, sym, qty, price),
        )
    else:  # SELL
        conn.execute(
            "UPDATE accounts SET krw_balance = krw_balance + %s WHERE account_id=%s",
            (price * qty - fee, acct),
        )
        conn.execute(
            "UPDATE positions SET quantity = quantity - %s "
            "WHERE account_id=%s AND symbol=%s",
            (qty, acct, sym),
        )

    conn.execute("UPDATE orders SET status='FILLED' WHERE order_id=%s", (ex["order_id"],))
    return True


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
                applied = apply_execution(conn, ex)
            consumer.commit(msg)
            if applied:
                print(f"[portfolio] applied {ex['side']} {ex['symbol']} "
                      f"qty={ex['quantity']} @ {ex['price']}")
    finally:
        consumer.close()
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[portfolio] stopped")
