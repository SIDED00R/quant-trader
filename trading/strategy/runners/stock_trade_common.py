"""주식 모의 일일매매 공용 헬퍼 (단일 책임: KR/US trade-once 공통부 — 계획·체결확인).

stock_trade_once(KR)·us_trade_once(US)가 시장 파라미터만 다른 동일 로직을 공유한다:
top-N long-or-cash 매매계획·잔고 폴링 체결확인. 시장별로 다른 부분(주문 종류: KR 시장가 vs
US 해외 지정가+체결추격(kis_chase)·거래소 라우팅)은 각 모듈에 남긴다. 최신 종가 조회는
common/marketdata/stock_price.py로 이동(app 이미지 공용). batch.ml.stock_score 의존 → Dockerfile.batch(trade) 전용.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from batch.ml.stock_score import score_latest
from common.marketdata.market_holidays import is_market_holiday, market_today
from common.postgres_client import open_pool
from trading.strategy.runners.weekly_marker import week_done

logger = logging.getLogger(__name__)

_EMPTY_PLAN = {"bar": None, "cash": 0.0, "targets": [], "buys": [], "sells": [], "ranked": None}
MAX_STALE_DAYS = 7   # 최신봉 신선도 상한(달력일) — 초과 시 매매 중단(잘못된 시세로 사이징 방지)


def skip_result(reason: str) -> dict:
    """가드 skip 반환 — plan 없이도 main의 요약 print·텔레그램 문안이 깨지지 않는 기본 키 포함."""
    return {**_EMPTY_PLAN, "placed": [], "skipped": reason}


def weekly_guard(market: str) -> str | None:
    """주문 전 공용 가드(스코어링·토스 호출보다 먼저): 주간 완료 → 휴장 순. skip 사유 또는 None."""
    open_pool()                                  # week_done이 pool 사용 — 반드시 선행
    today = market_today(market)
    if week_done(market, today):
        return f"이미 이번주 리밸런싱 완료({today})"
    if is_market_holiday(market, today):
        return f"{market} 휴장일({today}) — 다음 평일 재시도"
    return None


def build_plan(market: str, balance_fn, top_n: int, macro: bool) -> dict:
    """매매계획 산출(주문 없음). targets=top-N, buys=신규편입, sells=top-N 이탈(청산)."""
    latest, ranked = score_latest(market, top_n=top_n, macro=macro)
    stale = (market_today(market) - latest.date()).days if latest is not None else None
    if stale is None or stale > MAX_STALE_DAYS:
        raise RuntimeError(f"stock_candles_1d({market}) 신선도 초과 — 최신봉 {latest}"
                           f"(경과 {stale}일 > {MAX_STALE_DAYS}) — 갱신/백필 확인 필요")
    bal = balance_fn()
    held = {p["symbol"] for p in bal["positions"]}
    targets = list(ranked["symbol"])
    target_set = set(targets)
    return {
        "bar": str(latest), "cash": bal["cash"], "n_held": len(held), "bal": bal,   # bal 재사용(execute 재조회 방지)
        "targets": targets,
        "buys": [s for s in targets if s not in held],     # 신규 편입
        "sells": [s for s in held if s not in target_set],  # top-N 이탈 → 청산
        "ranked": ranked,
    }


def confirm_fills(balance_fn, before: dict, placed: list,
                  deadline: datetime | None = None, poll_sec: float = 2.0,
                  now_fn=None, sleep_fn=time.sleep) -> None:
    """접수≠체결 — 잔고 폴링으로 실제 체결 확인. placed 각 항목에 filled_qty/filled를 채운다(in-place).

    모의는 일별체결조회 미지원이라 주문 전 잔고(before)와 폴링 후 잔고 diff로 체결을 판정한다.
    항목의 side(BUY|SELL, 미지정=BUY)에 따라 증가/감소 방향으로 판정(매도 전량 체결로 잔고에서
    사라진 심볼도 감소로 커버). deadline(기본 now+12초)까지 폴링하되 접수 건수만큼 확인되면 조기
    종료 — KR 동시호가(15:30 매칭) 대기는 데드라인을 늘려 쓴다. 잔고 조회 일시 오류는 그 폴만
    건너뛰고 계속하며, 끝까지 미확인이면 filled=False로 남는다(마커 미기록 → 다음 평일 재시도).
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if deadline is None:
        deadline = now_fn() + timedelta(seconds=12)
    sides = {o["symbol"]: o.get("side", "BUY") for o in placed}
    accepted = sum(1 for o in placed if o.get("accepted"))
    filled: dict = {}
    while now_fn() < deadline:
        sleep_fn(poll_sec)
        try:
            after = {x["symbol"]: x["qty"] for x in balance_fn()["positions"]}
        except Exception as e:                   # 일시 오류 — 로그 남기고 다음 폴에서 재시도
            logger.error(f"잔고 조회 실패(폴 계속): {type(e).__name__}: {e}")
            continue
        deltas = {s: after.get(s, 0) - before.get(s, 0) for s in sides}
        filled = {s: round(abs(d)) for s, d in deltas.items()    # KIS 잔고 qty는 float — 표기용 정수화
                  if (d > 0 if sides[s] == "BUY" else d < 0)}
        if len(filled) >= accepted:
            break
    for o in placed:
        o["filled_qty"] = filled.get(o["symbol"], 0)
        o["filled"] = o["symbol"] in filled
