"""미국 주식 모의 일일매매 (단일 책임: US 챔피언 랭킹 → top-N → KIS 해외 모의 지정가매수).

KR판(stock_trade_once)의 US 대응. 차이: USD 통화·해외 잔고(us_balance)·해외 지정가주문(시장가 불가)·
거래소 라우팅(price_and_exchange)·미국장 시간(22:30~05:00 KST). 챔피언=OHLCV+펀더+13F+섹터(macro 제외),
스코어러 score_latest('US'). 지정가=현재가(없으면 직전 종가)×1.02(체결 buffer). 동일가중 USD 사이징.
⚠ 라이브 해외주문/시세는 미국장 시간 + KIS 모의 해외 시세도메인 실측 검증 필요(US장 마감 중엔 미검증).
"""
import argparse
import sys

from common import kis_balance
from common.kis_order import place_overseas_order
from common.kis_overseas_price import price_and_exchange
from trading.strategy.stock_trade_common import build_plan, confirm_fills, latest_closes

_BUFFER = 1.02   # 지정가 매수 buffer(체결 보장)


def plan(top_n: int = 20, macro: bool = False) -> dict:
    """매매계획(주문 없음). US 챔피언 top-N long-or-cash — build_plan 공용 정본."""
    return build_plan("US", kis_balance.us_balance, top_n, macro)


def execute(top_n: int = 20, macro: bool = False, live: bool = False, max_orders: int = 5) -> dict:
    """live=True면 buys 상위 max_orders개 KIS 해외 모의 지정가매수. 동일가중 USD 사이징·체결확인=잔고diff."""
    p = plan(top_n=top_n, macro=macro)
    if not live:
        return {**p, "placed": []}
    bal = kis_balance.us_balance()
    before = {x["symbol"]: x["qty"] for x in bal["positions"]}
    equity = bal["cash"] + sum(x["eval"] for x in bal["positions"])
    per = equity / max(1, top_n)
    buys = p["buys"][:max_orders]
    closes = latest_closes("US", buys)
    placed = []
    for sym in buys:
        px, exch = price_and_exchange(sym)
        ref = px or closes.get(sym)            # KIS 현재가 우선, 없으면 저장 종가
        if not ref:
            placed.append({"symbol": sym, "accepted": False, "msg": "시세/거래소 미확인"})
            continue
        limit = round(ref * _BUFFER, 2)
        qty = int(per // limit)
        if qty < 1:
            placed.append({"symbol": sym, "accepted": False, "msg": f"수량<1 (가격 {limit} > 목표 {per:,.0f})"})
            continue
        try:
            r = place_overseas_order(sym, "BUY", qty, limit, exch or "NASD")
            placed.append({"symbol": sym, "qty": qty, "limit": limit, "exch": exch or "NASD",
                           "accepted": str(r.get("rt_cd")) == "0", "msg": r.get("msg1")})
        except Exception as e:
            placed.append({"symbol": sym, "accepted": False, "error": f"{type(e).__name__}: {e}"})
    confirm_fills(kis_balance.us_balance, before, placed)   # 잔고 폴링 체결확인(접수≠체결)
    return {**p, "placed": placed}


def main(argv=None) -> int:
    """US 주간 모의 리밸런싱 진입점. --live 없으면 dry-run. 미국장 시간(22:30~05:00 KST)에 실행."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="US 주식 ML 챔피언 주간 모의 리밸런싱")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--max-orders", type=int, default=5, help="1회 주문 수 상한(안전)")
    ap.add_argument("--live", action="store_true", help="실제 KIS 해외 모의주문(미지정=dry-run)")
    a = ap.parse_args(argv)
    r = execute(top_n=a.top_n, max_orders=a.max_orders, live=a.live)
    print(f"[us-trade] bar={r['bar']} cash={r['cash']:,.2f} "
          f"targets={len(r['targets'])} buys={len(r['buys'])} sells={len(r['sells'])} live={a.live}")
    for o in r.get("placed", []):
        print("  ", o)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
