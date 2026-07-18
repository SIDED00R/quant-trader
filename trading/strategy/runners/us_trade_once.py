"""미국 주식 모의 일일매매 (단일 책임: US 챔피언 랭킹 → top-N → KIS 해외 모의 지정가매수).

KR판(stock_trade_once)의 US 대응. 차이: USD 통화·해외 잔고(us_balance)·해외 지정가주문(시장가 불가)·
거래소 라우팅(price_and_exchange)·미국장 시간(22:30~05:00 KST). 챔피언=OHLCV+펀더+13F+섹터(macro 제외),
스코어러 score_latest('US'). 체결 보장 = kis_chase.place_and_chase(지정가 buffer 1.02→1.04 추격 —
잔고 diff 폴링으로 확인, 미체결 잔여는 취소 후 가격 재조회·재주문). 동일가중 USD 사이징.
⚠ 라이브 해외주문/시세는 미국장 시간 + KIS 모의 해외 시세도메인 실측 검증 필요(US장 마감 중엔 미검증).
"""
import argparse
import sys
import traceback
from datetime import datetime, timezone

from batch.candles.refresh_stock_daily import refresh
from common import notify_telegram
from common.broker import kis_balance
from common.equity.equity_snapshot import record_stock_snapshot
from common.broker.kis_chase import place_and_chase
from common.broker.kis_overseas_price import price_and_exchange
from common.marketdata.market_holidays import market_today
from common.marketdata.market_hours import market_open, market_seconds_to_close
from common.marketdata.stock_price import latest_closes
from trading.strategy.core.notify_messages import error_message, stock_message
from trading.strategy.runners.stock_trade_common import build_plan, skip_result, weekly_guard
from trading.strategy.runners.weekly_marker import completed, mark_week_done

_BUFFER = 1.02   # 동일가중 수량 산정용 1차 지정가 버퍼(kis_chase의 1차 BUY 버퍼와 동일)
_MIN_SECONDS_TO_CLOSE = 300   # 정규장 마감까지 이 이하로 남으면 발주 스킵(부분 거부 방지 — 다음 평일 재시도)


def plan(top_n: int = 20, macro: bool = False) -> dict:
    """매매계획(주문 없음). US 챔피언 top-N long-or-cash — build_plan 공용 정본."""
    return build_plan("US", kis_balance.us_balance, top_n, macro)


def execute(top_n: int = 20, macro: bool = False, live: bool = False, max_orders: int = 5) -> dict:
    """live=True면 buys 상위 max_orders개 KIS 해외 모의 매수. 동일가중 USD 사이징·체결보장=place_and_chase."""
    if not live:
        return {**plan(top_n=top_n, macro=macro), "placed": [], "skipped": None}
    reason = weekly_guard("US")                  # 가드 먼저 — 휴장·완료 주엔 스코어링·토스 호출 안 함
    if reason:
        return skip_result(reason)
    now = datetime.now(timezone.utc)             # 정규장 세션 가드 — 마감 도달분 거부(REJECTED)·부분체결 후 마커 트랩 방지
    if not market_open("US", now):
        return skip_result("US 정규장 아님 — 다음 평일 재시도")
    ttc = market_seconds_to_close("US", now)
    if ttc is not None and ttc < _MIN_SECONDS_TO_CLOSE:
        return skip_result(f"US 마감 임박({ttc:.0f}s) — 다음 평일 재시도")
    refresh(["US"], log=print)                   # 일봉 증분 갱신(종목별 격리 — 부분 실패는 신선도 게이트가 방어)
    p = plan(top_n=top_n, macro=macro)
    today = market_today("US")
    bal = p["bal"]                                            # build_plan이 이미 조회 — us_balance 4콜 재조회 방지(#218 C3)
    equity = bal["cash"] + sum(x["eval"] for x in bal["positions"])
    per = equity / max(1, top_n)
    buys = p["buys"][:max_orders]
    closes = latest_closes("US", buys)
    placed = []
    for sym in buys:
        px, exch = price_and_exchange(sym)
        ref = px or closes.get(sym)            # KIS 현재가 우선, 없으면 저장 종가
        if not ref:
            placed.append({"symbol": sym, "accepted": False, "filled": False, "filled_qty": 0,
                           "msg": "시세/거래소 미확인"})
            continue
        limit = round(ref * _BUFFER, 2)
        qty = int(per // limit)
        if qty < 1:
            placed.append({"symbol": sym, "accepted": False, "filled": False, "filled_qty": 0,
                           "msg": f"수량<1 (가격 {limit} > 목표 {per:,.0f})"})
            continue
        r = place_and_chase("US", sym, "BUY", qty, ref_price=ref, exchange=exch or "NASD")
        rej = r["attempts"][0].get("error") if r["status"] == "REJECTED" and r["attempts"] else None
        placed.append({"symbol": sym, "qty": qty, "status": r["status"], "attempts": r["attempts"],
                       "accepted": r["status"] != "REJECTED", "msg": rej,   # 거부 시 실제 KIS 사유(msg_cd/msg1) 노출
                       "filled_qty": r["filled_qty"], "filled": r["filled_qty"] > 0})
    if completed(p, placed):                                # 체결됨(또는 매수할 것 없음) → 그 주 완료 기록
        mark_week_done("US", today)
    return {**p, "placed": placed, "skipped": None}


def main(argv=None) -> int:
    """US 주간 모의 리밸런싱 진입점. --live 없으면 dry-run. 미국장 시간(22:30~05:00 KST)에 실행.

    종료코드: 0=정상 / 70=오류(텔레그램 통보 완료) / 1=오류인데 통보도 실패(startup 폴백이 발송).
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="US 주식 ML 챔피언 주간 모의 리밸런싱")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--max-orders", type=int, default=5, help="1회 주문 수 상한(안전)")
    ap.add_argument("--live", action="store_true", help="실제 KIS 해외 모의주문(미지정=dry-run)")
    a = ap.parse_args(argv)
    try:
        r = execute(top_n=a.top_n, max_orders=a.max_orders, live=a.live)
    except Exception as e:
        traceback.print_exc()
        sent = notify_telegram.send(error_message("US 주식", e))
        return 70 if sent else 1
    if a.live:   # 자산 곡선 원천 — 주간 스킵 날도 매일 1포인트(비치명, KR판과 동일)
        record_stock_snapshot("US", kis_balance.us_balance)
    print(f"[us-trade] bar={r['bar']} cash={r['cash']:,.2f} "
          f"targets={len(r['targets'])} buys={len(r['buys'])} sells={len(r['sells'])} live={a.live}")
    if r.get("skipped"):
        print(f"  skip: {r['skipped']}")
    for o in r.get("placed", []):
        print("  ", o)
    notify_telegram.send(stock_message("US 주식", r, live=a.live))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
