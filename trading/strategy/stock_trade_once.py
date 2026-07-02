"""주식 모의 일일매매 (단일 책임: ML 챔피언 랭킹 → top-N long-only → KIS 모의주문).

dry_run=True(기본)면 주문 없이 매매계획만 반환(검증용). live면 KIS 모의주문 발행.
KR 유니버스. 잔고/포지션 소스 = KIS(kis_balance). 동일가중 top-N.
⚠ 현 execute()는 **신규 편입(buys)만 발주** — 이탈 청산(plan의 sells)은 산출만 하고 미발주(검증 단계).
주문은 무재시도 자금경로(kis_order) — 라이브는 호출처에서 명시적으로 켠다.
"""
import argparse
import sys

from common import kis_balance
from common.kis_order import place_domestic_order
from common.market_holidays import is_market_holiday, market_today
from common.postgres_client import open_pool
from common.stock_price import latest_closes
from trading.strategy.stock_trade_common import build_plan, confirm_fills
from trading.strategy.weekly_marker import completed, mark_week_done, week_done


def plan(top_n: int = 30, macro: bool = False) -> dict:
    """매매계획 산출(주문 없음). KR top-N long-or-cash — build_plan 공용 정본."""
    return build_plan("KR", kis_balance.kr_balance, top_n, macro)


def execute(top_n: int = 30, macro: bool = False, live: bool = False, max_orders: int = 5) -> dict:
    """매매계획 실행. live=False면 계획만. live=True면 buys 상위 max_orders개 KIS 모의 매수.

    동일가중: 목표금액 = 평가자산/top_n, 수량 = floor(목표금액/현재가)(정수주). 가격>목표면 건너뜀.
    안전: max_orders로 1회 주문 수 제한(검증 단계).
    """
    p = plan(top_n=top_n, macro=macro)
    if not live:
        return {**p, "placed": [], "skipped": None}
    open_pool()
    today = market_today("KR")
    if week_done("KR", today):                       # 그 주 이미 매매함 → 평일 재부팅 skip
        return {**p, "placed": [], "skipped": f"이미 이번주 리밸런싱 완료({today})"}
    if is_market_holiday("KR", today):               # KR 휴장 셋은 빈 채 — 보통 체결기반 재시도로 처리
        return {**p, "placed": [], "skipped": f"KR 휴장일({today}) — 다음 평일 재시도"}
    bal = kis_balance.kr_balance()
    before = {x["symbol"]: x["qty"] for x in bal["positions"]}   # 체결확인 기준선(모의는 일별체결조회 미지원)
    equity = bal["cash"] + sum(x["eval"] for x in bal["positions"])
    per_name = equity / max(1, top_n)                            # 동일가중 목표금액
    buys = p["buys"][:max_orders]
    closes = latest_closes("KR", buys)
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
    confirm_fills(kis_balance.kr_balance, before, placed)   # 잔고 폴링 체결확인(접수≠체결)
    if completed(p, placed):                                # 체결됨(또는 매수할 것 없음) → 그 주 완료 기록
        mark_week_done("KR", today)
    return {**p, "placed": placed, "skipped": None}


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
    if r.get("skipped"):
        print(f"  skip: {r['skipped']}")
    for o in r.get("placed", []):
        print("  ", o)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
