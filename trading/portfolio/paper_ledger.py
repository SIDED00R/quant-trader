"""주식 페이퍼(모의) 체결 (단일 책임: 실주문 없이 시장가 체결을 Postgres 장부에 기록).

실계좌·브로커 없이 코인 시뮬 장부(portfolio.apply_execution)를 그대로 재사용한다:
orders(PENDING) INSERT → 동기 체결(검증·현금/포지션 갱신·FILLED). 수수료는 KR 실계좌와 동일 가정 —
매수=수수료(FEE_RATE), 매도=수수료+거래세(STOCK_SELL_TAX_RATE). KIS 실계좌(ML 전략)와 공정 비교용.
account_id는 accounts에 별도 행으로 시드(db/migrations/postgres/0001_baseline.sql). auto_trade=FALSE 유지(코인 잡 비대상).
"""
import uuid
from decimal import Decimal

from common.config import FEE_RATE, STOCK_SELL_TAX_RATE
from trading.portfolio.updater import apply_execution

_FEE_QUANT = Decimal("0.0001")
_SELL_RATE = FEE_RATE + STOCK_SELL_TAX_RATE


def kr_fee(side: str, price, qty) -> Decimal:
    """왕복 비용 반영 수수료 — SELL은 거래세 포함. 코인 경로와 동일한 0.0001 양자화."""
    rate = _SELL_RATE if side == "SELL" else FEE_RATE
    return (Decimal(str(price)) * Decimal(str(qty)) * rate).quantize(_FEE_QUANT)


def positions(conn, acct: str) -> dict:
    """{symbol: quantity(int)} — 보유 수량>0. KR은 정수주."""
    rows = conn.execute(
        "SELECT symbol, quantity FROM positions WHERE account_id=%s AND quantity>0", (acct,)).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def cash(conn, acct: str) -> Decimal:
    row = conn.execute("SELECT krw_balance FROM accounts WHERE account_id=%s", (acct,)).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)


def simulate_fill(conn, acct: str, symbol: str, side: str, qty: int, price) -> str:
    """주문(PENDING, outbox 없음) INSERT 후 동기 체결. 반환 'applied'|'rejected'|'duplicate'."""
    oid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO orders (order_id, account_id, symbol, side, type, price, quantity, status) "
        "VALUES (%s,%s,%s,%s,'MARKET',%s,%s,'PENDING')",
        (oid, acct, symbol, side, price, qty))
    ex = {"execution_id": str(uuid.uuid4()), "order_id": oid, "account_id": acct,
          "symbol": symbol, "side": side, "price": price, "quantity": qty,
          "fee": kr_fee(side, price, qty)}
    return apply_execution(conn, ex)
