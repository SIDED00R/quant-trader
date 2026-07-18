"""계좌·포지션 읽기 (단일 책임: 자동매매 계정 목록·보유수량·현금 조회 — Postgres).

trade_once(프로덕션 일배치)와 commander(로컬 dev 스트리밍)가 공유한다 — 동일 구현 2벌의 발산 방지.
"""
from decimal import Decimal

from common.postgres_client import pool


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
