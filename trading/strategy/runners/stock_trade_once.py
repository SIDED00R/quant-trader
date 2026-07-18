"""주식 모의 일일매매 (단일 책임: ML 챔피언 랭킹 → top-N long-only → KIS 모의주문).

dry_run=True(기본)면 주문 없이 매매계획만 반환(검증용). live면 KIS 모의주문 발행.
KR 유니버스. 잔고/포지션 소스 = KIS(kis_balance). 동일가중 top-N.
주간 전량 리밸런싱: 이탈 청산(sells) 전량 매도 먼저 → 신규 편입(buys) 매수, 모두 시장가.
체결확인은 15:35 KST 데드라인 — 동시호가(15:20~15:30) 접수분의 15:30 매칭을 커버한다.
주문은 무재시도 자금경로(kis_order) — 라이브는 호출처에서 명시적으로 켠다.
"""
import argparse
import sys
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from batch.candles.refresh_stock_daily import refresh
from common import notify_telegram
from common.broker import kis_balance
from common.equity.equity_snapshot import record_stock_snapshot
from common.broker.kis_order import place_domestic_order
from common.marketdata.market_holidays import market_today
from common.marketdata.stock_price import latest_closes
from trading.strategy.core.notify_messages import error_message, stock_message
from trading.strategy.runners.stock_trade_common import build_plan, confirm_fills, skip_result, weekly_guard
from trading.strategy.runners.weekly_marker import completed, mark_week_done

_KST = ZoneInfo("Asia/Seoul")


def _fill_deadline(now: datetime | None = None) -> datetime:
    """체결확인 데드라인 = 당일 15:35 KST(동시호가 매칭 15:30 + 여유 5분), 최소 now+60초.

    동시호가(15:20~15:30) 접수분은 15:30 매칭까지 기다려야 체결이 잔고에 보인다.
    15:35 이후 시작된 비정상 지연 실행은 최소창만 확인(장마감 거부 → 마커 미기록 → 재시도).
    """
    now = now.astimezone(_KST) if now else datetime.now(_KST)
    auction = now.replace(hour=15, minute=35, second=0, microsecond=0)
    return max(auction, now + timedelta(seconds=60))


def plan(top_n: int = 30, macro: bool = False) -> dict:
    """매매계획 산출(주문 없음). KR top-N long-or-cash — build_plan 공용 정본."""
    return build_plan("KR", kis_balance.kr_balance, top_n, macro)


def _place(sym: str, side: str, qty: int) -> dict:
    """국내 시장가 단건 발주 → placed 항목 dict(접수/오류 기록 — 매도·매수 공용)."""
    try:
        resp = place_domestic_order(sym, side, qty)
        return {"symbol": sym, "side": side, "qty": qty,
                "accepted": str(resp.get("rt_cd")) == "0", "msg": resp.get("msg1")}
    except Exception as e:
        return {"symbol": sym, "side": side, "qty": qty, "accepted": False,
                "error": f"{type(e).__name__}: {e}"}


def execute(top_n: int = 30, macro: bool = False, live: bool = False, max_orders: int = 5) -> dict:
    """매매계획 실행. live=False면 계획만. live=True면 sells 전량 매도 → buys 상위 max_orders개 매수(모두 시장가).

    동일가중: 목표금액 = 평가자산/top_n, 수량 = floor(목표금액/현재가)(정수주). 가격>목표면 건너뜀.
    매도 먼저(청산 우선 — 연속장에선 매도 체결대금이 당일 매수여력에 반영. 동시호가는 15:30 동시
    매칭이라 대금 선확보 불가 — 매수여력 부족 거부는 KIS 응답(msg)으로 관측) · 캡은 매수에만
    (청산 잔존 방지 — 매도는 ≤top_n 자연 상한).
    """
    if not live:
        return {**plan(top_n=top_n, macro=macro), "placed": [], "skipped": None}
    reason = weekly_guard("KR")                  # 가드 먼저 — 휴장·완료 주엔 스코어링·토스 호출 안 함
    if reason:
        return skip_result(reason)
    refresh(["KR", "US"], log=print)              # KR 모델이 US 컨텍스트 피처를 쓰므로 둘 다 갱신
    p = plan(top_n=top_n, macro=macro)
    today = market_today("KR")
    bal = p["bal"]                                              # build_plan이 이미 조회 — 재조회 방지(#218 C3)
    before = {x["symbol"]: x["qty"] for x in bal["positions"]}   # 체결확인 기준선 겸 이탈 매도 수량(보유 전량)
    equity = bal["cash"] + sum(x["eval"] for x in bal["positions"])
    per_name = equity / max(1, top_n)                            # 동일가중 목표금액
    buys = p["buys"][:max_orders]
    closes = latest_closes("KR", buys)
    placed = []
    for sym in p["sells"]:                                       # 이탈 청산 먼저 — 보유 전량 시장가(캡 없음)
        placed.append(_place(sym, "SELL", round(before.get(sym, 0))))   # sells⊆보유(qty≥1) — build_plan 동일 스냅샷
    for sym in buys:
        px = closes.get(sym)
        qty = int(per_name // px) if px and px > 0 else 0
        if qty < 1:
            placed.append({"symbol": sym, "side": "BUY", "qty": 0, "accepted": False,
                           "msg": f"수량<1 (가격 {px} > 목표 {per_name:,.0f})" if px else "가격 미확인"})
            continue
        placed.append(_place(sym, "BUY", qty))
    confirm_fills(kis_balance.kr_balance, before, placed,    # 잔고 폴링 체결확인(접수≠체결)
                  deadline=_fill_deadline(), poll_sec=5.0)   # 동시호가 15:30 매칭까지 커버
    if completed(p, placed, rebalance_sells=True):           # 체결됨(또는 할 일 없음) → 그 주 완료 기록
        mark_week_done("KR", today)
    return {**p, "placed": placed, "skipped": None}


def main(argv=None) -> int:
    """주간 모의 리밸런싱 진입점(스케줄러가 호출). --live 없으면 dry-run.

    종료코드: 0=정상 / 70=오류(텔레그램 통보 완료) / 1=오류인데 통보도 실패(startup 폴백이 발송).
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="주식 ML 챔피언 주간 모의 리밸런싱(KR)")
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--max-orders", type=int, default=5, help="1회 매수 주문 수 상한(안전 — 매도는 캡 없음)")
    ap.add_argument("--live", action="store_true", help="실제 KIS 모의주문(미지정=dry-run)")
    a = ap.parse_args(argv)
    try:
        r = execute(top_n=a.top_n, max_orders=a.max_orders, live=a.live)
    except Exception as e:
        traceback.print_exc()
        sent = notify_telegram.send(error_message("KR 주식", e))
        return 70 if sent else 1
    if a.live:   # 자산 곡선 원천 — 주간 스킵 날도 매일 1포인트(체결확인 뒤 재조회=체결 후 상태, 비치명)
        record_stock_snapshot("KR", kis_balance.kr_balance)
    print(f"[stock-trade] bar={r['bar']} cash={r['cash']:,.0f} "
          f"targets={len(r['targets'])} buys={len(r['buys'])} sells={len(r['sells'])} live={a.live}")
    if r.get("skipped"):
        print(f"  skip: {r['skipped']}")
    for o in r.get("placed", []):
        print("  ", o)
    notify_telegram.send(stock_message("KR 주식", r, live=a.live))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
