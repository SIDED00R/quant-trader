"""성과 라우트 (단일 책임: 체결 이력 → 실현손익·승률·수수료 집계).

executions를 FIFO로 매칭해 라운드트립 실현손익과 승률을 산출한다(라이브 모의매매 성과 패널용).
미실현은 대시보드가 포지션×현재가로 별도 계산. 순손익(평가자산−초기자본)은 /account가 제공.
2초 폴링 대응: **체결 count 기반 증분 캐시** — executions는 append-only라 count가 그대로면
전체 FIFO 재계산을 생략한다(count 조회는 (account_id, executed_at) 인덱스 온리 스캔).
"""
from collections import defaultdict, deque

from fastapi import APIRouter, Depends

from api.cache import cache_get_stale, cache_set
from api.security import current_account_id
from common.postgres_client import pool

router = APIRouter()


@router.get("/performance")
def performance(account_id: str = Depends(current_account_id)):
    key = f"performance:{account_id}"
    with pool.connection() as conn:
        n = conn.execute(
            "SELECT count(*) FROM executions WHERE account_id=%s", (account_id,)
        ).fetchone()[0]
        cached = cache_get_stale(key)
        if cached is not None and cached["n"] == n:
            return cached["payload"]
        rows = conn.execute(
            "SELECT symbol, side, price, quantity, fee FROM executions "
            "WHERE account_id=%s ORDER BY executed_at, execution_id",
            (account_id,),
        ).fetchall()

    lots: dict[str, deque] = defaultdict(deque)   # symbol -> [ [남은수량, 단위원가(매수수수료 포함)] ... ] FIFO
    realized = 0.0          # 실현손익(매도 시 확정, 매수·매도 수수료 반영)
    fees = 0.0
    wins = losses = 0       # 라운드트립(매도 청산) 단위 승/패
    n_buy = n_sell = 0

    for symbol, side, price, qty, fee in rows:
        price, qty, fee = float(price), float(qty), float(fee)
        fees += fee
        if side == "BUY":
            n_buy += 1
            lots[symbol].append([qty, (price * qty + fee) / qty])   # 단위원가 = (체결+수수료)/수량
        else:  # SELL — FIFO로 매수 로트 소진하며 실현손익 확정
            n_sell += 1
            remaining, matched_cost = qty, 0.0
            dq = lots[symbol]
            while remaining > 1e-18 and dq:
                lot = dq[0]
                take = min(remaining, lot[0])
                matched_cost += take * lot[1]
                lot[0] -= take
                remaining -= take
                if lot[0] <= 1e-18:
                    dq.popleft()
            pnl = (price * qty - fee) - matched_cost   # 매도대금(수수료 차감) − 매칭 매수원가
            realized += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1

    closed = wins + losses
    payload = {
        "realized_pnl": realized,
        "total_fees": fees,
        "num_trades": n_buy + n_sell,
        "num_buys": n_buy,
        "num_sells": n_sell,
        "closed_trades": closed,
        "wins": wins,
        "win_rate": (wins / closed) if closed else None,
    }
    cache_set(key, {"n": n, "payload": payload})
    return payload
