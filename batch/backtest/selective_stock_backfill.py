"""주식 일봉 선별 재백필 (단일 책임: 재조정/데이터갭 종목만 골라 전체 재백필).

매월 유지보수의 '일봉 풀 재백필'을 대체한다. 전체 유니버스를 매번 통째로 재수신하는 대신
아래 종목만 전체 재백필하고, 나머지는 매 거래일 refresh_stock_daily(14일)가 최신을 유지한다:
  (1) 데이터 갭 — 최신 저장 봉이 STALE_DAYS보다 오래됨(refresh 누락 보충).
  (2) 수정주가 재조정 — 과거 앵커 종가가 토스 재조회와 달라짐(분할/배당 소급 재조정).

⚠ 재조정 감지는 refresh 창(14일) '밖'의 과거 앵커를 비교해야 성립한다. 최근 구간은 매 거래일
refresh가 이미 새 조정계수로 덮어써서 DB=토스로 일치 → 재조정이 드러나지 않기 때문이다.
"""
from datetime import datetime, timezone

from batch.backtest import backfill_stock_daily
from batch.backtest.toss_daily import fetch_daily
from common.clickhouse_client import create_client

_ANCHORS = (20, 45, 90)          # (참고용) refresh(14일) 밖 과거 앵커 개념 — 실제 비교는 _PROBE_DAYS 전 구간
_STALE_DAYS = 20                 # 최신 봉이 이보다 오래되면 refresh 누락으로 보고 전체 재백필
_TOL = 0.005                     # 종가 상대오차 허용(분할=배수차라 명확, 배당 재조정도 대부분 초과)
_PROBE_DAYS = max(_ANCHORS) + 15  # 감지용 조회 구간(앵커 커버 + 여유) — 1페이지로 충분


def _needs_full(symbol: str, ch, log=print) -> bool:
    """전체 재백필이 필요한가 — 데이터 갭 또는 수정주가 재조정."""
    rows = ch.query(
        "SELECT window_start, close FROM stock_candles_1d "
        "WHERE symbol={s:String} AND window_start >= now() - INTERVAL {d:UInt32} DAY "
        "ORDER BY window_start", parameters={"s": symbol, "d": _PROBE_DAYS}).result_rows
    if not rows:
        return True                              # 신규/빈 종목 → 전체
    latest = rows[-1][0]
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - latest).days > _STALE_DAYS:
        return True                              # refresh 누락 → 전체로 보충
    db_close = {ws.date(): float(c) for ws, c in rows}
    try:
        toss = fetch_daily(symbol, _PROBE_DAYS, log=lambda *a: None)
    except Exception as e:
        log(f"[선별백필] {symbol} 감지 fetch 실패 → skip: {type(e).__name__}")
        return False                             # 감지 실패는 보수적으로 skip(refresh가 커버)
    toss_close = {r[1].date(): float(r[5]) for r in toss}   # toss row: [sym,ws,o,h,l,c,v,cur,mkt], close=idx5
    for d, dbc in db_close.items():
        tc = toss_close.get(d)
        if tc and abs(dbc - tc) / tc > _TOL:
            return True                          # 앵커 종가 불일치 = 수정주가 재조정
    return False


def run(symbols: list, days: int, log=print) -> int:
    """symbols 중 재백필이 필요한 종목만 골라 전체 재백필(days). 나머지는 refresh가 최신 유지."""
    ch = create_client()
    full = [s for s in symbols if _needs_full(s, ch, log)]
    log(f"[선별백필] 재백필 {len(full)}/{len(symbols)}종목 (나머지 {len(symbols) - len(full)}은 최신·무재조정 → skip)")
    if full:
        return backfill_stock_daily.main(["--symbols", ",".join(full), "--days", str(days)])
    return 0
