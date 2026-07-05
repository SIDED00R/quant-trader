"""수동 주식 주문 실행기 (단일 책임: 예약/즉시 수동주문의 도래분 집행 — lifespan 백그라운드 태스크).

api 컨테이너(매매 VM 온디맨드 대시보드 모드에서 기동 — 상시 아님)가 25s 주기로 due PENDING을
클레임해 place_and_chase(체결 추격)로 매매한다. 예약 주문은 대시보드가 떠 있는 동안만 집행된다.
- 클레임 = UPDATE … WHERE id=(SELECT … FOR UPDATE SKIP LOCKED) — 단일 인스턴스 전제(compose api 1개)지만 다중에도 안전.
- 금액(amount) 주문은 실행 시점 현재가로 수량 환산(floor). 시세 미확인·수량<1 → FAILED.
- 재시작 고아 복구: 30분 넘게 PLACED로 남은 주문(집행 중 재시작)은 FAILED 처리 — 잔고diff 진실은 대시보드가 보여준다.
KIS 자격증명은 api 컨테이너 env(#173)로 이미 주입돼 있다.
"""
import asyncio
import logging

from psycopg.types.json import Json

from common import notify_telegram
from common.kis_chase import place_and_chase
from common.kis_domestic_price import current_price
from common.kis_overseas_price import price_and_exchange
from common.postgres_client import pool
from common.stock_price import latest_closes

log = logging.getLogger(__name__)

_IDLE_SEC = 25
_ORPHAN_MIN = 30


def _recover_orphans() -> None:
    with pool.connection() as conn:
        rows = conn.execute(
            "UPDATE manual_stock_orders SET status='FAILED', updated_at=now(), "
            "detail = COALESCE(detail, '{}'::jsonb) || '{\"error\": \"실행 중 재시작(고아 복구)\"}'::jsonb "
            f"WHERE status='PLACED' AND updated_at < now() - interval '{_ORPHAN_MIN} minutes' RETURNING id",
        ).fetchall()
    if rows:
        log.warning("고아 수동주문 %d건 FAILED 처리: %s", len(rows), [r[0] for r in rows])
        notify_telegram.send(f"🔴 수동주문 고아 복구 {len(rows)}건 FAILED 처리: {[r[0] for r in rows]} — 대시보드 확인")


def _claim_one():
    """due PENDING 1건을 PLACED로 전이하며 가져온다(경합 안전). 없으면 None."""
    with pool.connection() as conn:
        return conn.execute(
            "UPDATE manual_stock_orders SET status='PLACED', updated_at=now() "
            "WHERE id = (SELECT id FROM manual_stock_orders "
            "            WHERE status='PENDING' AND scheduled_at <= now() "
            "            ORDER BY scheduled_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, market, symbol, side, qty, amount",
        ).fetchone()


def _finish(order_id: int, status: str, detail: dict) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE manual_stock_orders SET status=%s, detail=%s, updated_at=now() WHERE id=%s",
            (status, Json(detail), order_id),
        )


def _resolve(market: str, symbol: str, qty, amount):
    """(수량, 참조가, 거래소) — amount면 현재가(폴백: 저장 종가)로 환산. 불가 시 ValueError."""
    if market == "US":
        ref, exch = price_and_exchange(symbol)
    else:
        ref, exch = current_price(symbol), None
    if not ref:
        ref = latest_closes(market, [symbol]).get(symbol)
    if qty is None:
        if not ref:
            raise ValueError("시세 미확인 — 금액을 수량으로 환산할 수 없음")
        qty = int(float(amount) // float(ref))
        if qty < 1:
            raise ValueError(f"수량<1 (참조가 {ref}, 금액 {amount})")
    return int(qty), ref, exch


def _execute(row) -> None:
    order_id, market, symbol, side, qty, amount = row
    try:
        q, ref, exch = _resolve(market, symbol, qty, amount)
    except Exception as e:
        _finish(order_id, "FAILED", {"error": f"{type(e).__name__}: {e}"})
        # 예약 주문은 대시보드를 닫은 뒤 실행되므로 DB 기록만으론 조용히 묻힌다 — 텔레그램으로도 통보.
        notify_telegram.send(f"🔴 수동주문 실패 #{order_id} [{market}] {side} {symbol} — {type(e).__name__}: {e}")
        return
    try:
        r = place_and_chase(market, symbol, side, q, ref_price=ref, exchange=exch)
    except Exception as e:
        # 주문 전 기준선 잔고조회 등에서 raise(fail-loud 전환) — 여기서 안 잡으면 run()의 제네릭
        # 핸들러가 로그만 남기고 주문이 PLACED로 고착된다(고아 복구는 재기동 시 1회뿐).
        _finish(order_id, "FAILED", {"error": f"{type(e).__name__}: {e}"})
        notify_telegram.send(f"🔴 수동주문 실패 #{order_id} [{market}] {side} {symbol} — {type(e).__name__}: {e}")
        return
    detail = {"resolved_qty": q, "ref_price": ref, **r}
    if r["filled_qty"] >= q:
        _finish(order_id, "FILLED", detail)
    elif r["filled_qty"] > 0:                      # 부분체결 — 잔여는 chase가 이미 취소함
        detail["partial"] = True
        _finish(order_id, "FILLED", detail)
    else:
        _finish(order_id, "FAILED", detail)
        err = (r.get("attempts") or [{}])[0].get("error")
        notify_telegram.send(f"🔴 수동주문 실패 #{order_id} [{market}] {side} {symbol} x{q} — {r['status']}"
                             + (f" ({err})" if err else ""))
    log.info("수동주문 #%s %s %s %s x%s → %s", order_id, market, side, symbol, q, r["status"])


async def run() -> None:
    """lifespan 태스크 본체 — 취소(CancelledError)로 종료."""
    await asyncio.to_thread(_recover_orphans)
    while True:
        try:
            row = await asyncio.to_thread(_claim_one)
            if row is not None:
                await asyncio.to_thread(_execute, row)
                continue                            # 연속 due 주문은 쉬지 않고 처리
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # DB 순단 등 — 실행기는 죽지 않는다
            log.warning("수동주문 실행기 오류(계속): %s", e)
        await asyncio.sleep(_IDLE_SEC)
