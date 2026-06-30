"""주식 모의 일일매매 (단일 책임: ML 챔피언 랭킹 → top-N long-only → KIS 모의주문).

dry_run=True(기본)면 주문 없이 매매계획만 반환(검증용). live면 KIS 모의주문 발행.
KR 유니버스. 잔고/포지션 소스 = KIS(kis_balance). 동일가중 top-N long-or-cash.
주문은 무재시도 자금경로(kis_order) — 라이브는 호출처에서 명시적으로 켠다.
"""
import argparse
import sys
import time

from batch.ml.stock_score import score_latest
from common import kis_balance
from common.clickhouse_client import create_client
from common.kis_order import place_domestic_order


def _latest_closes(symbols: list) -> dict:
    """KR 최신 일봉 종가(symbol→close). 동일가중 수량 산정용."""
    if not symbols:
        return {}
    rows = create_client().query(
        "SELECT symbol, argMax(close, window_start) FROM stock_candles_1d "
        "WHERE market='KR' AND symbol IN {syms:Array(String)} GROUP BY symbol",
        parameters={"syms": list(symbols)}).result_rows
    return {r[0]: float(r[1]) for r in rows}


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
    """매매계획 실행. live=False면 계획만. live=True면 buys 상위 max_orders개 KIS 모의 매수.

    동일가중: 목표금액 = 평가자산/top_n, 수량 = floor(목표금액/현재가)(정수주). 가격>목표면 건너뜀.
    안전: max_orders로 1회 주문 수 제한(검증 단계).
    """
    p = plan(top_n=top_n, macro=macro)
    if not live:
        return {**p, "placed": []}
    bal = kis_balance.kr_balance()
    before = {x["symbol"]: x["qty"] for x in bal["positions"]}   # 체결확인 기준선(모의는 일별체결조회 미지원)
    equity = bal["cash"] + sum(x["eval"] for x in bal["positions"])
    per_name = equity / max(1, top_n)                            # 동일가중 목표금액
    buys = p["buys"][:max_orders]
    closes = _latest_closes(buys)
    placed = []
    for sym in buys:
        px = closes.get(sym)
        qty = int(per_name // px) if px and px > 0 else 0
        if qty < 1:
            placed.append({"symbol": sym, "qty": 0, "accepted": False,
                           "msg": f"수량<1 (가격 {px} > 목표 {per_name:,.0f})" if px else "가격 미확인"})
            continue
        try:
            resp = place_domestic_order(sym, "BUY", qty)
            placed.append({"symbol": sym, "qty": qty, "accepted": str(resp.get("rt_cd")) == "0", "msg": resp.get("msg1")})
        except Exception as e:
            placed.append({"symbol": sym, "qty": qty, "accepted": False, "error": f"{type(e).__name__}: {e}"})
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


def main(argv=None) -> int:
    """주간 모의 리밸런싱 진입점(스케줄러가 호출). --live 없으면 dry-run."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="주식 ML 챔피언 주간 모의 리밸런싱(KR)")
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--max-orders", type=int, default=5, help="1회 주문 수 상한(안전)")
    ap.add_argument("--live", action="store_true", help="실제 KIS 모의주문(미지정=dry-run)")
    a = ap.parse_args(argv)
    r = execute(top_n=a.top_n, max_orders=a.max_orders, live=a.live)
    print(f"[stock-trade] bar={r['bar']} cash={r['cash']:,.0f} "
          f"targets={len(r['targets'])} buys={len(r['buys'])} sells={len(r['sells'])} live={a.live}")
    for o in r.get("placed", []):
        print("  ", o)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
