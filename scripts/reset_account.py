"""계정 초기화 (단일 책임: 모의 계정을 초기 가상자본으로 리셋).

포지션·주문·체결·아웃박스를 비우고 krw_balance를 INITIAL_BALANCE로 되돌린다(과거 매매로 녹은 가상계정 재시작용).
FK 순서(executions→orders→positions) 지켜 삭제. 파괴적이므로 --account 또는 --all 명시 필수.
예) python -m scripts.reset_account --account demo
    python -m scripts.reset_account --all
"""
import argparse
import sys

from common.config import INITIAL_BALANCE
from common.postgres_client import close_pool, open_pool, pool


def reset(account_ids: list[str]) -> None:
    with pool.connection() as conn:
        for acct in account_ids:
            conn.execute("DELETE FROM executions WHERE account_id=%s", (acct,))
            conn.execute(
                "DELETE FROM order_outbox WHERE order_id IN (SELECT order_id FROM orders WHERE account_id=%s)",
                (acct,))
            conn.execute("DELETE FROM orders WHERE account_id=%s", (acct,))
            conn.execute("DELETE FROM positions WHERE account_id=%s", (acct,))
            n = conn.execute("UPDATE accounts SET krw_balance=%s WHERE account_id=%s",
                             (INITIAL_BALANCE, acct)).rowcount
            print(f"[reset] {acct}: 잔고 {INITIAL_BALANCE:,} 원으로 리셋, 포지션/주문/체결 삭제"
                  + ("" if n else "  (※ 계정 없음)"))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="모의 계정 초기화(초기 가상자본으로 리셋)")
    p.add_argument("--account", help="대상 account_id")
    p.add_argument("--all", action="store_true", help="모든 계정 초기화")
    a = p.parse_args(argv)
    open_pool()
    try:
        if a.all:
            with pool.connection() as conn:
                ids = [r[0] for r in conn.execute("SELECT account_id FROM accounts").fetchall()]
        elif a.account:
            ids = [a.account]
        else:
            print("--account <id> 또는 --all 이 필요합니다(파괴적 작업).", file=sys.stderr)
            return 2
        if not ids:
            print("[reset] 대상 계정 없음")
            return 0
        reset(ids)
    finally:
        close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
