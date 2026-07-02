"""KIS 주문 체결 추격 (단일 책임: 주문→체결확인→미체결 취소·재주문 루프로 체결 보장).

접수≠체결 — 주문 후 잔고 diff 폴링(백오프, 최대 KIS_CONFIRM_WINDOW_SEC)으로 체결을 확인하고,
US 지정가가 미체결이면 잔여를 취소한 뒤 현재가를 재조회해 버퍼를 키운 지정가로 재주문한다
(최대 max_attempts회). KR은 시장가라 미체결 잔존이 없다 — 폴링 후 종료(취소·재주문 없음).
모의는 일별체결조회 미지원이라 체결 판정은 잔고 diff가 유일(stock_trade_common.confirm_fills와 동일 원리).
안전 원칙: **취소 성공이 확인되기 전에는 재주문하지 않는다**(이중체결 방지) — 취소 실패 시
잔고만 재확인하고 반환해, 주간 재시도(자동매매)·FAILED 기록(수동주문)에 위임한다.
"""
import time

from common import kis_balance
from common.config import KIS_CONFIRM_WINDOW_SEC
from common.kis_cancel import cancel_overseas_order
from common.kis_order import place_domestic_order, place_overseas_order
from common.kis_overseas_price import price_and_exchange

_BUY_BUFFERS = (1.02, 1.03, 1.04)    # 시도별 지정가 버퍼 — BUY는 현재가 위로(체결 우선)
_SELL_BUFFERS = (0.98, 0.97, 0.96)   # SELL은 현재가 아래로
_POLL_DELAYS = (2, 3, 4, 6, 8, 10)   # 잔고 폴링 백오프(초, 마지막 값 유지)


def _held_qty(market: str, symbol: str) -> float:
    """현재 보유수량(잔고 조회). 체결 판정 기준선/현재값."""
    bal = kis_balance.kr_balance() if market == "KR" else kis_balance.us_balance()
    return next((p["qty"] for p in bal["positions"] if p["symbol"] == symbol), 0.0)


def _filled_since(market: str, symbol: str, side: str, base: float) -> int:
    """기준선(base) 대비 체결 수량. BUY=증가분, SELL=감소분(음수는 0)."""
    now = _held_qty(market, symbol)
    return int(max(0.0, (now - base) if side == "BUY" else (base - now)))


def _poll_fill(market: str, symbol: str, side: str, base: float, want: int, window: int) -> int:
    """잔고 diff 폴링 — want 수량 체결되거나 window(초) 소진까지. 누적 체결 수량 반환."""
    waited, i, filled = 0.0, 0, 0
    while waited < window:
        d = _POLL_DELAYS[min(i, len(_POLL_DELAYS) - 1)]
        time.sleep(d)
        waited += d
        i += 1
        filled = _filled_since(market, symbol, side, base)
        if filled >= want:
            break
    return filled


def place_and_chase(market: str, symbol: str, side: str, qty: int, *,
                    ref_price: float | None = None, exchange: str | None = None,
                    max_attempts: int = 3,
                    confirm_window: int = KIS_CONFIRM_WINDOW_SEC) -> dict:
    """주문 후 체결까지 추격. market: KR(시장가)|US(지정가+추격). side: BUY|SELL.

    ref_price/exchange는 US 1차 시도용(미지정 시 KIS 현재가 조회). 예외를 던지지 않고
    {"status": FILLED|PARTIAL|UNFILLED|REJECTED, "requested_qty", "filled_qty", "attempts":[...]}를 반환한다.
    """
    attempts: list[dict] = []
    remaining = qty
    base = _held_qty(market, symbol)                 # 체결확인 기준선(주문 전 보유수량)

    for n in range(max_attempts if market == "US" else 1):
        att: dict = {"qty": remaining}
        try:
            if market == "KR":
                r = place_domestic_order(symbol, side, remaining)
            else:
                px, exch = (ref_price, exchange) if n == 0 and ref_price else price_and_exchange(symbol)
                if not px:
                    att["error"] = "시세/거래소 미확인"
                    attempts.append(att)
                    break
                buffers = _BUY_BUFFERS if side == "BUY" else _SELL_BUFFERS
                buf = buffers[min(n, len(buffers) - 1)]
                att["limit"] = round(px * buf, 2)
                att["exch"] = exch or "NASD"
                r = place_overseas_order(symbol, side, remaining, att["limit"], att["exch"])
            att["odno"] = (r.get("output") or {}).get("ODNO")
        except Exception as e:                       # 주문 자체 거부(장외·잔고부족 등) → 추격 종료
            att["error"] = f"{type(e).__name__}: {e}"
            attempts.append(att)
            break

        filled_total = _poll_fill(market, symbol, side, base, qty, confirm_window)
        att["filled_total"] = filled_total
        attempts.append(att)
        remaining = qty - filled_total
        if remaining <= 0 or market == "KR":         # 전량 체결 or KR 시장가(잔존 없음) → 종료
            break

        try:                                         # US 미체결 잔여 취소 → 다음 시도에서 재주문
            cancel_overseas_order(symbol, att["odno"], remaining, att["exch"])
            att["cancel"] = "ok"
            remaining = qty - _filled_since(market, symbol, side, base)   # 취소 직전 막판 체결 반영(중복 주문 방지)
            if remaining <= 0:
                break
        except Exception as e:                       # 취소 미확인 — 재주문 금지, 잔고만 재확인 후 종료
            att["cancel"] = f"{type(e).__name__}: {e}"
            remaining = qty - _filled_since(market, symbol, side, base)
            break

    filled = qty - remaining
    if remaining <= 0:
        status = "FILLED"
    elif filled > 0:
        status = "PARTIAL"
    elif attempts and "error" in attempts[0]:
        status = "REJECTED"                          # 1차 주문부터 거부 — 아무것도 접수되지 않음
    else:
        status = "UNFILLED"
    return {"status": status, "requested_qty": qty, "filled_qty": filled, "attempts": attempts}
