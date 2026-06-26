"""자동매매 봇 (단일 책임: 규율 기반 SMA 전략 → 주문).

market.ticks 구독(latest). 종목별 단기/장기 SMA 이격 밴드로 추세 상태를 판정하고,
확인봉(CONFIRM_TICKS)으로 확정된 추세에서 매 틱 매매 '자격'을 재평가한다.
신호 감지(추세 상태)와 매매 자격(보유·쿨다운·최소보유·체결대기)을 분리해,
익절/손절 후 추세가 유지되면 재진입하고 일부 계정이 신호를 놓치지 않게 한다.

규칙:
- 매수(진입): 확정 추세가 BUY 이고 현재 틱도 BUY 밴드 & 미보유 & 재진입 쿨다운 경과
- 매도(청산, 우선순위, 매 틱): ① 손절(평단 -X%) ② 익절(평단 +X%) ③ 트레일링(고점 무장 후 되돌림)
                                ④ 데드크로스(확정 추세 SELL, 최소보유·쿨다운 후)
- 종목당 1포지션, MARKET 주문, 손절/익절 기준은 positions.avg_buy_price(DB).

견고성:
- place_order는 PENDING만 기록하고 체결은 비동기(relay→엔진→포트폴리오)다. 매수/매도 발행 후
  체결 반영 전 같은 포지션을 다시 주문하지 않도록 '미체결 주문 존재'(orders PENDING)로 보류한다.
- 기동 후 STRATEGY_WARMUP_SEC 동안 신규 진입/데드크로스 청산을 보류한다(재시작 직후 쿨다운·
  최소보유 인메모리 리셋으로 인한 즉시 매매 방지). 손절/익절/트레일링은 워밍업과 무관(자본보호).
- latest offset 구독이라 기동/재시작 시 과거 틱을 재생하지 않는다(룩어헤드 금지).
- 인메모리 상태(peak/진입시각/청산시각/보류/추세)는 재시작 시 리셋되고 포지션은 DB가 진실.
"""
import json
import time
from collections import deque
from decimal import Decimal

from common.config import (
    MIN_ORDER_KRW,
    SMA_LONG,
    SMA_SHORT,
    STRATEGY_CONFIRM_TICKS,
    STRATEGY_COOLDOWN_SEC,
    STRATEGY_ENTRY_BAND,
    STRATEGY_MAX_POSITIONS,
    STRATEGY_MIN_HOLD_SEC,
    STRATEGY_ORDER_FRACTION_MAX,
    STRATEGY_ORDER_FRACTION_MIN,
    STRATEGY_STOP_LOSS_PCT,
    STRATEGY_STRONG_GAP,
    STRATEGY_TAKE_PROFIT_PCT,
    STRATEGY_TRAIL_ARM_PCT,
    STRATEGY_TRAIL_GIVEBACK_PCT,
    STRATEGY_WARMUP_SEC,
    TOPIC_TICKS,
)
from common.kafka_client import create_consumer
from common.order_writer import place_order
from common.postgres_client import close_pool, open_pool, pool

GROUP_ID = "strategy"
SETTLE_SEC = 5.0       # 주문 후 체결 반영 전 중복 주문 방지 빠른 윈도우(이후엔 PENDING 조회로 확인)
ACCOUNTS_TTL = 3.0     # auto_trade 계정 목록 캐시 TTL(초)
POSITIONS_TTL = 1.5    # 보유 포지션 캐시 bulk 갱신 주기(초) — 매 틱 DB조회 제거(전체 종목 스케일링)

# 퍼센트 → 비율(Decimal)
_STOP = STRATEGY_STOP_LOSS_PCT / Decimal(100)
_TAKE = STRATEGY_TAKE_PROFIT_PCT / Decimal(100)
_ARM = STRATEGY_TRAIL_ARM_PCT / Decimal(100)
_GIVEBACK = STRATEGY_TRAIL_GIVEBACK_PCT / Decimal(100)

# 인메모리 상태 (재시작 시 리셋; 포지션 진실은 DB)
_last_price: dict[str, Decimal] = {}
_peak: dict[tuple, Decimal] = {}        # (acct,symbol) -> 보유 중 최고 미실현 수익률
_entry_time: dict[tuple, float] = {}    # (acct,symbol) -> 진입 monotonic
_last_exit: dict[tuple, float] = {}     # (acct,symbol) -> 마지막 청산 monotonic(재진입 쿨다운)
_exit_pending: dict[tuple, float] = {}  # (acct,symbol) -> 매도 발행 monotonic(중복 매도 방지)
_entry_pending: dict[tuple, float] = {} # (acct,symbol) -> 매수 발행 monotonic(중복 매수 방지)
_reserved: dict[tuple, Decimal] = {}    # (acct,symbol) -> 미체결 매수 예산(가용잔고 차감 → 버스트 과발주 방지)
_started_at = 0.0                       # 프로세스 기동 monotonic(워밍업)
_accounts: list[str] = []
_accounts_at = -1e9
_positions: dict[tuple, tuple] = {}     # (acct,symbol) -> (qty, avg) 보유분 캐시(bulk 갱신)
_positions_at = -1e9


def enabled_accounts(now: float) -> list[str]:
    """auto_trade=TRUE 계정 목록(짧은 TTL 캐시 — 토글 반영은 ACCOUNTS_TTL 내)."""
    global _accounts, _accounts_at
    if now - _accounts_at >= ACCOUNTS_TTL:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT account_id FROM accounts WHERE auto_trade=TRUE"
            ).fetchall()
        _accounts = [r[0] for r in rows]
        _accounts_at = now
    return _accounts


def refresh_positions(now: float, accounts: list[str]) -> None:
    """enabled 계정의 보유분(quantity>0)을 한 번의 bulk 쿼리로 캐시 갱신(TTL).

    매 틱 계정별 DB 조회를 없애 전체 종목(고빈도 틱)에서도 DB 부하를 일정하게 유지한다.
    체결 미반영 윈도우는 _entry_pending/_exit_pending 보류 가드가 별도로 막는다.
    """
    global _positions, _positions_at
    if now - _positions_at < POSITIONS_TTL:
        return
    _positions_at = now
    if not accounts:
        _positions = {}
        return
    with pool.connection() as conn:
        prows = conn.execute(
            "SELECT account_id, symbol, quantity, avg_buy_price FROM positions "
            "WHERE quantity > 0 AND account_id = ANY(%s)",
            (accounts,),
        ).fetchall()
        orows = conn.execute(
            "SELECT account_id, symbol, side FROM orders "
            "WHERE status='PENDING' AND side IN ('BUY','SELL') AND account_id = ANY(%s)",
            (accounts,),
        ).fetchall()
    _positions = {
        (r[0], r[1]): (Decimal(str(r[2])), Decimal(str(r[3] or 0))) for r in prows
    }
    # 미체결 추적을 실제 PENDING 주문과 동기화: 더 이상 PENDING이 아닌(체결/거부) 보류는 해제
    # → _entry_pending 누수로 인한 open_count 동결·예약 예산 누수 방지
    acct_set = set(accounts)
    pending_buy = {(r[0], r[1]) for r in orows if r[2] == "BUY"}
    pending_sell = {(r[0], r[1]) for r in orows if r[2] == "SELL"}
    for key in [k for k in _entry_pending if k[0] in acct_set and k not in pending_buy]:
        _release_entry(key)
    for key in [k for k in _exit_pending if k[0] in acct_set and k not in pending_sell]:
        _exit_pending.pop(key, None)


def held_position(account_id: str, symbol: str) -> tuple[Decimal, Decimal]:
    """(보유수량, 평균매수가). 포지션 캐시에서 조회(없으면 (0,0))."""
    return _positions.get((account_id, symbol), (Decimal(0), Decimal(0)))


def open_count(account_id: str) -> int:
    """보유 + 매수 대기 중인 종목 수(최대 보유 캡 판정용)."""
    syms = {s for (a, s) in _positions if a == account_id}
    syms |= {s for (a, s) in _entry_pending if a == account_id}
    return len(syms)


def account_balance(account_id: str) -> Decimal:
    """현재 KRW 현금 잔고(매수 금액 산정용). 매수 직전에만 조회."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT krw_balance FROM accounts WHERE account_id=%s", (account_id,)
        ).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)


def _has_pending(account_id: str, symbol: str, side: str) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM orders WHERE account_id=%s AND symbol=%s AND side=%s "
            "AND status='PENDING' LIMIT 1",
            (account_id, symbol, side),
        ).fetchone()
    return row is not None


def _awaiting(book: dict, key: tuple, now: float, side: str) -> bool:
    """직전 발행 주문이 아직 체결/거부되지 않았는지(중복 주문 방지, 순수 판정).

    빠른 경로: 발행 후 SETTLE_SEC 이내면 True(DB 조회 없음).
    이후엔 orders 테이블에 해당 PENDING 주문이 남아있는지로 확정.
    보류 해제(누수 방지)는 refresh_positions의 PENDING 동기화/보유 감지가 전담한다(여기선 부수효과 없음).
    """
    ts = book.get(key)
    if ts is None:
        return False
    if now - ts < SETTLE_SEC:
        return True
    return _has_pending(key[0], key[1], side)


def _release_entry(key: tuple) -> None:
    """매수 보류 + 예약 예산 동시 해제(체결/거부 확정 시)."""
    _entry_pending.pop(key, None)
    _reserved.pop(key, None)


def reserved_for(account_id: str) -> Decimal:
    """해당 계정의 미체결 매수 예약 예산 합계."""
    return sum((v for (a, _), v in _reserved.items() if a == account_id), Decimal(0))


def sma_gap(prices: deque) -> Decimal | None:
    """단기/장기 이평선의 부호있는 간격 (short-long)/long. 데이터 부족/비정상이면 None."""
    if len(prices) < SMA_LONG:
        return None
    p = list(prices)
    short = sum(p[-SMA_SHORT:]) / Decimal(SMA_SHORT)
    long_ = sum(p[-SMA_LONG:]) / Decimal(SMA_LONG)
    if long_ <= 0:
        return None
    return (short - long_) / long_


def sma_state(prices: deque) -> str | None:
    """이격 밴드 기반 추세 상태: 'BUY' | 'SELL' | None(NEUTRAL/데이터부족)."""
    gap = sma_gap(prices)
    if gap is None:
        return None
    if gap >= STRATEGY_ENTRY_BAND:
        return "BUY"
    if gap <= -STRATEGY_ENTRY_BAND:
        return "SELL"
    return None


def position_fraction(gap: Decimal) -> Decimal:
    """신호 강도(이평선 간격)에 비례한 매수 비율: 약하면 MIN, 강하면 MAX (그 사이 선형)."""
    span = STRATEGY_STRONG_GAP - STRATEGY_ENTRY_BAND
    if span <= 0:
        return STRATEGY_ORDER_FRACTION_MAX
    t = (abs(gap) - STRATEGY_ENTRY_BAND) / span
    t = max(Decimal(0), min(Decimal(1), t))
    return STRATEGY_ORDER_FRACTION_MIN + (STRATEGY_ORDER_FRACTION_MAX - STRATEGY_ORDER_FRACTION_MIN) * t


def liquidation_reason(pnl: Decimal, peak: Decimal) -> str | None:
    """청산 사유(우선순위): 손절 > 익절 > 트레일링. 없으면 None."""
    if pnl <= -_STOP:
        return "STOP"
    if pnl >= _TAKE:
        return "TAKE"
    if peak >= _ARM and pnl <= peak - _GIVEBACK:
        return "TRAIL"
    return None


def _sell(acct: str, symbol: str, qty: Decimal, reason: str, now: float) -> None:
    place_order(acct, symbol, "SELL", "MARKET", qty)
    _last_exit[(acct, symbol)] = now
    _exit_pending[(acct, symbol)] = now
    _peak.pop((acct, symbol), None)
    _entry_time.pop((acct, symbol), None)
    print(f"[strategy] SELL {symbol} qty={qty} acct={acct} ({reason})")


def check_liquidations(symbol: str, price: Decimal, now: float) -> None:
    """보유 포지션 매 틱 청산: 손절 > 익절 > 트레일링 (한 틱 1회). 워밍업과 무관(자본보호)."""
    for acct in enabled_accounts(now):
        key = (acct, symbol)
        qty, avg = held_position(acct, symbol)
        if qty <= 0 or avg <= 0:
            _exit_pending.pop(key, None)  # 청산 체결 확인 → 보류/고점 해제
            _peak.pop(key, None)
            continue
        if _awaiting(_exit_pending, key, now, "SELL"):
            continue  # 직전 매도 체결 대기
        pnl = price / avg - 1
        peak = _peak.get(key, pnl)
        if pnl > peak:
            peak = pnl
        _peak[key] = peak
        reason = liquidation_reason(pnl, peak)
        if reason:
            _sell(acct, symbol, qty, reason, now)


def enter(symbol: str, now: float, gap: Decimal) -> None:
    """확정 BUY 추세 진입(미보유 & 재진입 쿨다운 경과).

    매수액 = 현재 현금 × 신호강도 비율(이평선 간격이 클수록 큼). 매 틱 호출되며 계정별 가드가 과진입 방어.
    """
    if now - _started_at < STRATEGY_WARMUP_SEC:
        return
    price = _last_price.get(symbol)
    if price is None or price <= 0:
        return
    frac = position_fraction(gap)
    for acct in enabled_accounts(now):
        key = (acct, symbol)
        held, _ = held_position(acct, symbol)
        if held > 0:
            _release_entry(key)  # 매수 체결 확인 → 보류/예약 해제
            continue
        if _awaiting(_entry_pending, key, now, "BUY"):
            continue  # 직전 매수 체결 대기(중복 매수 방지)
        if now - _last_exit.get(key, -1e9) < STRATEGY_COOLDOWN_SEC:
            continue
        if open_count(acct) >= STRATEGY_MAX_POSITIONS:
            continue  # 최대 보유 종목 수 도달 → 신규 진입 보류
        # 가용잔고 = 현금 − 미체결 매수 예약(버스트 동시진입 과발주 방지)
        available = account_balance(acct) - reserved_for(acct)
        budget = available * frac if available > 0 else Decimal(0)
        if budget < MIN_ORDER_KRW:
            continue
        qty = (budget / price).quantize(Decimal("0.00000001"))
        if qty <= 0:
            continue
        place_order(acct, symbol, "BUY", "MARKET", qty)
        _entry_pending[key] = now
        _reserved[key] = budget
        _entry_time[key] = now
        _peak.pop(key, None)
        print(f"[strategy] BUY {symbol} qty={qty} (~{budget:.0f}KRW, frac={frac * 100:.1f}%) acct={acct} (ENTRY)")


def exit_deadcross(symbol: str, now: float) -> None:
    """확정 SELL 추세 청산(최소보유·쿨다운 경과). 매 틱 호출 — 자격 충족 시 청산(유실 방지)."""
    if now - _started_at < STRATEGY_WARMUP_SEC:
        return
    for acct in enabled_accounts(now):
        key = (acct, symbol)
        qty, _ = held_position(acct, symbol)
        if qty <= 0:
            continue
        if _awaiting(_exit_pending, key, now, "SELL"):
            continue
        if now - _entry_time.get(key, -1e9) < STRATEGY_MIN_HOLD_SEC:
            continue
        if now - _last_exit.get(key, -1e9) < STRATEGY_COOLDOWN_SEC:
            continue
        _sell(acct, symbol, qty, "DEADCROSS", now)


def run() -> None:
    global _started_at
    open_pool()
    _started_at = time.monotonic()
    consumer = create_consumer(GROUP_ID, enable_auto_commit=True, auto_offset_reset="latest")
    consumer.subscribe([TOPIC_TICKS])
    prices: dict[str, deque] = {}
    state: dict[str, str] = {}       # 확정 추세(symbol -> 'BUY'/'SELL'), 확인봉으로만 전환
    pending: dict[str, list] = {}    # symbol -> [후보상태, 연속틱수] (확인봉)
    print(f"[strategy] started SMA({SMA_SHORT}/{SMA_LONG}) band={STRATEGY_ENTRY_BAND} "
          f"confirm={STRATEGY_CONFIRM_TICKS} stop={STRATEGY_STOP_LOSS_PCT}% take={STRATEGY_TAKE_PROFIT_PCT}% "
          f"trail({STRATEGY_TRAIL_ARM_PCT}/{STRATEGY_TRAIL_GIVEBACK_PCT})% "
          f"order={STRATEGY_ORDER_FRACTION_MIN * 100:.0f}-{STRATEGY_ORDER_FRACTION_MAX * 100:.0f}% maxpos={STRATEGY_MAX_POSITIONS} "
          f"cooldown={STRATEGY_COOLDOWN_SEC}s minhold={STRATEGY_MIN_HOLD_SEC}s warmup={STRATEGY_WARMUP_SEC}s")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            data = json.loads(msg.value())
            symbol = data["symbol"]
            price = Decimal(str(data["price"]))
            _last_price[symbol] = price
            dq = prices.setdefault(symbol, deque(maxlen=SMA_LONG))
            dq.append(price)
            now = time.monotonic()
            refresh_positions(now, enabled_accounts(now))  # 보유 포지션 캐시 갱신(TTL)

            # (1) 보유 포지션 매 틱 청산 우선 (손절/익절/트레일링)
            check_liquidations(symbol, price, now)

            # (2) 확인봉으로 확정 추세(state) 갱신
            raw = sma_state(dq)
            if raw is not None and raw != state.get(symbol):
                pend = pending.get(symbol)
                if pend and pend[0] == raw:
                    pend[1] += 1
                else:
                    pend = [raw, 1]
                    pending[symbol] = pend
                if pend[1] >= STRATEGY_CONFIRM_TICKS:
                    state[symbol] = raw
                    pending.pop(symbol, None)
            else:
                pending.pop(symbol, None)

            # (3) 확정 추세가 현재 틱과 일치할 때만 매매 자격 재평가
            #     (NEUTRAL/미확정 반전 틱은 건너뜀. 계정별 보유·쿨다운 가드가 과매매 방어)
            if state.get(symbol) == "BUY" and raw == "BUY":
                enter(symbol, now, sma_gap(dq))
            elif state.get(symbol) == "SELL" and raw == "SELL":
                exit_deadcross(symbol, now)
    finally:
        consumer.close()
        close_pool()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[strategy] stopped")
