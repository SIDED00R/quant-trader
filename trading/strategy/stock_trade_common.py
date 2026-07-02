"""주식 모의 일일매매 공용 헬퍼 (단일 책임: KR/US trade-once 공통부 — 계획·체결확인).

stock_trade_once(KR)·us_trade_once(US)가 시장 파라미터만 다른 동일 로직을 공유한다:
top-N long-or-cash 매매계획·잔고 폴링 체결확인. 시장별로 다른 부분(주문 종류: KR 시장가 vs
US 해외 지정가+체결추격(kis_chase)·거래소 라우팅)은 각 모듈에 남긴다. 최신 종가 조회는
common/stock_price.py로 이동(app 이미지 공용). batch.ml.stock_score 의존 → Dockerfile.batch(trade) 전용.
"""
import time

from batch.ml.stock_score import score_latest


def build_plan(market: str, balance_fn, top_n: int, macro: bool) -> dict:
    """매매계획 산출(주문 없음). targets=top-N, buys=신규편입, sells=top-N 이탈(청산)."""
    latest, ranked = score_latest(market, top_n=top_n, macro=macro)
    bal = balance_fn()
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


def confirm_fills(balance_fn, before: dict, placed: list) -> None:
    """접수≠체결 — 잔고 폴링으로 실제 체결 확인. placed 각 항목에 filled_qty/filled를 채운다(in-place).

    모의는 일별체결조회 미지원이라 매수 전 잔고(before)와 폴링 후 잔고 diff로 체결을 판정한다.
    """
    syms = [o["symbol"] for o in placed]
    accepted = sum(1 for o in placed if o.get("accepted"))
    filled: dict = {}
    for _ in range(6):
        time.sleep(2)
        after = {x["symbol"]: x["qty"] for x in balance_fn()["positions"]}
        filled = {s: after.get(s, 0) - before.get(s, 0) for s in syms if after.get(s, 0) > before.get(s, 0)}
        if len(filled) >= accepted:
            break
    for o in placed:
        o["filled_qty"] = filled.get(o["symbol"], 0)
        o["filled"] = o["symbol"] in filled
