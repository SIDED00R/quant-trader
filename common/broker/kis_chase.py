"""KIS 주문 체결 추격 (단일 책임: 주문→체결확인→미체결 취소·재주문 루프로 체결 보장).

접수≠체결 — 주문 후 잔고 diff 폴링(백오프, 최대 KIS_CONFIRM_WINDOW_SEC)으로 체결을 확인하고,
US 지정가가 미체결이면 잔여를 취소한 뒤 현재가를 재조회해 버퍼를 키운 지정가로 재주문한다
(최대 max_attempts회). KR은 시장가라 미체결 잔존이 없다 — 폴링 후 종료(취소·재주문 없음).
모의는 일별체결조회 미지원이라 체결 판정은 잔고 diff가 유일(stock_trade_common.confirm_fills와 동일 원리).
안전 원칙: **확인 불가 상태에서는 재주문하지 않는다**(이중체결 방지) —
- 주문 전 기준선 조회 실패 = 아직 주문 없음 → 예외 전파(안전 중단, 콜러가 통보/재시도).
- 폴링 중 잔고 조회 실패 = 크게 로그 + 마지막 관측값 유지(보수적 '미체결' 간주 — 취소→재주문은
  취소 성공 확인이 선행되므로 이중체결은 불가).
- 취소 후 재확인 실패 = 잔고 미확인 → 재주문 금지, 기록 후 종료(주간 재시도·FAILED 기록에 위임).
"""
import logging
import time

from common.broker import kis_balance
from common.config import KIS_CONFIRM_WINDOW_SEC
from common.broker.kis_cancel import cancel_overseas_order
from common.broker.kis_order import place_domestic_order, place_overseas_order
from common.broker.kis_overseas_price import price_and_exchange

logger = logging.getLogger(__name__)

_BUY_BUFFERS = (1.02, 1.03, 1.04)    # 시도별 지정가 버퍼 — BUY는 현재가 위로(체결 우선)
_SELL_BUFFERS = (0.98, 0.97, 0.96)   # SELL은 현재가 아래로
_POLL_DELAYS = (2, 3, 4, 6, 8, 10)   # 잔고 폴링 백오프(초, 마지막 값 유지)


def _held_qty(market: str, symbol: str, exchange: str | None = None) -> float:
    """현재 보유수량(잔고 조회). 체결 판정 기준선/현재값. US는 주문 거래소만 조회(us_balance 4콜 회피)."""
    if market == "KR":
        bal = kis_balance.kr_balance()
        return next((p["qty"] for p in bal["positions"] if p["symbol"] == symbol), 0.0)
    return kis_balance.us_held_qty(symbol, exchange or "NASD")


def _filled_since(market: str, symbol: str, side: str, base: float, exchange: str | None = None) -> int:
    """기준선(base) 대비 체결 수량. BUY=증가분, SELL=감소분(음수는 0)."""
    now = _held_qty(market, symbol, exchange)
    return int(max(0.0, (now - base) if side == "BUY" else (base - now)))


def _poll_fill(market: str, symbol: str, side: str, base: float, want: int, window: int,
               exchange: str | None = None) -> int:
    """잔고 diff 폴링 — want 수량 체결되거나 window(초) 소진까지. 누적 체결 수량 반환.

    조회 1회 실패는 크게 로그 후 폴링 지속(마지막 관측값 유지 = 보수적 '미체결' 간주).
    """
    waited, i, filled = 0.0, 0, 0
    while waited < window:
        d = _POLL_DELAYS[min(i, len(_POLL_DELAYS) - 1)]
        time.sleep(d)
        waited += d
        i += 1
        try:
            filled = _filled_since(market, symbol, side, base, exchange)
        except Exception as e:
            logger.error(f"체결확인 잔고조회 실패({type(e).__name__}: {e}) — 폴링 계속, 미체결 간주")
            continue
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
    # US 체결확인을 단일 거래소 조회로 하려면 exchange를 먼저 확정한다(us_balance 4콜→1콜). KR은 불필요.
    px0 = ref_price
    if market == "US" and not exchange:
        px0, exchange = price_and_exchange(symbol)
    base = _held_qty(market, symbol, exchange)        # 체결확인 기준선(주문 전 보유수량, US=단일 거래소)

    for n in range(max_attempts if market == "US" else 1):
        att: dict = {"qty": remaining}
        try:
            if market == "KR":
                r = place_domestic_order(symbol, side, remaining)
            else:
                px = px0 if (n == 0 and px0) else price_and_exchange(symbol)[0]   # 재시도는 현재가만 재조회(거래소 고정)
                if not px:
                    att["error"] = "시세/거래소 미확인"
                    attempts.append(att)
                    break
                buffers = _BUY_BUFFERS if side == "BUY" else _SELL_BUFFERS
                buf = buffers[min(n, len(buffers) - 1)]
                att["limit"] = round(px * buf, 2)
                att["exch"] = exchange or "NASD"
                r = place_overseas_order(symbol, side, remaining, att["limit"], att["exch"])
            att["odno"] = (r.get("output") or {}).get("ODNO")
        except Exception as e:                       # 주문 자체 거부(장외·잔고부족 등) → 추격 종료
            att["error"] = f"{type(e).__name__}: {e}"
            attempts.append(att)
            break

        filled_total = _poll_fill(market, symbol, side, base, qty, confirm_window, exchange)
        att["filled_total"] = filled_total
        attempts.append(att)
        remaining = qty - filled_total
        if remaining <= 0 or market == "KR":         # 전량 체결 or KR 시장가(잔존 없음) → 종료
            break

        try:                                         # US 미체결 잔여 취소 → 다음 시도에서 재주문
            cancel_overseas_order(symbol, att["odno"], remaining, att["exch"])
            att["cancel"] = "ok"
            try:
                remaining = qty - _filled_since(market, symbol, side, base, exchange)   # 취소 직전 막판 체결 반영(중복 주문 방지)
            except Exception as e:                   # 취소 후 재확인 실패 — 막판 체결 미확인이면 재주문 금지
                att["recheck"] = f"{type(e).__name__}: {e}"
                logger.error(f"취소 후 잔고 재확인 실패({e}) — 재주문 금지, 종료")
                break
            if remaining <= 0:
                break
        except Exception as e:                       # 취소 미확인 — 재주문 금지, 잔고만 재확인 후 종료
            att["cancel"] = f"{type(e).__name__}: {e}"
            try:
                remaining = qty - _filled_since(market, symbol, side, base, exchange)
            except Exception as e2:
                att["recheck"] = f"{type(e2).__name__}: {e2}"
                logger.error(f"취소 미확인 + 잔고 재확인 실패({e2}) — 마지막 관측값으로 종료")
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
