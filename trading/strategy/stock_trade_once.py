"""주식 모의 일일매매 (단일 책임: ML 챔피언 랭킹 → top-N long-only → KIS 모의주문).

dry_run=True(기본)면 주문 없이 매매계획만 반환(검증용). live면 KIS 모의주문 발행.
KR 유니버스. 잔고/포지션 소스 = KIS(kis_balance). 동일가중 top-N long-or-cash.
주문은 무재시도 자금경로(kis_order) — 라이브는 호출처에서 명시적으로 켠다.
"""
import time

from batch.ml.stock_score import score_latest
from common import kis_balance
from common.kis_order import place_domestic_order


def plan(top_n: int = 30, macro: bool = False) -> dict:
    """매매계획 산출(주문 없음). targets=top-N, buys=신규편입, sells=이탈."""
    latest, ranked = score_latest("KR", top_n=top_n, macro=macro)
    bal = kis_balance.kr_balance()
    held = {p["symbol"] for p in bal["positions"]}
    targets = list(ranked["symbol"])
    target_set = set(targets)
    return {
        "bar": str(latest), "cash": bal["cash"], "n_held": len(held),
        "targets": targets,
        "buys": [s for s in targets if s not in held],     # 신규 편입
        "sells": [s for s in held if s not in target_set],  # top-N 이탈 → 청산
        "ranked": ranked,
    }


def execute(top_n: int = 30, macro: bool = False, live: bool = False, max_orders: int = 5) -> dict:
    """매매계획 실행. live=False면 계획만. live=True면 buys 상위 max_orders개 KIS 모의 시장가 매수.

    안전: max_orders로 1회 주문 수 제한(검증 단계). 동일가중 수량은 cash/top_n//price.
    """
    p = plan(top_n=top_n, macro=macro)
    if not live:
        return {**p, "placed": []}
    # 체결 확인 기준선 — KIS 모의는 일별체결조회 미지원("내역 없음") → 잔고 포지션 증가분이 진실
    before = {x["symbol"]: x["qty"] for x in kis_balance.kr_balance()["positions"]}
    placed = []
    for sym in p["buys"][:max_orders]:
        try:
            resp = place_domestic_order(sym, "BUY", 1)   # 검증단계 1주씩(동일가중 사이징은 후속)
            placed.append({"symbol": sym, "accepted": str(resp.get("rt_cd")) == "0", "msg": resp.get("msg1")})
        except Exception as e:
            placed.append({"symbol": sym, "accepted": False, "error": f"{type(e).__name__}: {e}"})
    # 잔고 폴링으로 실제 체결 확인(접수≠체결)
    syms = [o["symbol"] for o in placed]
    filled: dict = {}
    for _ in range(6):
        time.sleep(2)
        after = {x["symbol"]: x["qty"] for x in kis_balance.kr_balance()["positions"]}
        filled = {s: after.get(s, 0) - before.get(s, 0) for s in syms if after.get(s, 0) > before.get(s, 0)}
        if len(filled) >= sum(1 for o in placed if o.get("accepted")):
            break
    for o in placed:
        o["filled_qty"] = filled.get(o["symbol"], 0)
        o["filled"] = o["symbol"] in filled
    return {**p, "placed": placed}
