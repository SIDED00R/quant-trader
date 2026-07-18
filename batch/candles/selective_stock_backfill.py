"""주식 일봉 선별 재백필 (단일 책임: 재조정/데이터갭 종목만 골라 전체 재백필).

매월 유지보수의 '일봉 풀 재백필'을 대체한다. 전체 유니버스를 매번 통째로 재수신하는 대신
아래 종목만 전체 재백필하고, 나머지는 매 거래일 refresh_stock_daily(14일)가 최신을 유지한다:
  (1) 데이터 갭 — 최신 저장 봉이 STALE_DAYS보다 오래됨(refresh 누락 보충). ※ 꼬리 신선도만 본다:
      최신 봉이 신선하면 중간에 뚫린 내부 갭은 감지 못 한다(refresh가 매일 14일을 채워 실무상 드묾).
  (2) 수정주가 재조정 — 과거 구간 종가가 토스 재조회와 달라짐(분할/배당 소급 재조정).

⚠ 재조정 감지는 refresh 창(14일) '밖'의 과거를 비교해야 성립한다(최근은 refresh가 새 계수로 덮음).
  월간 cadence 전제 — 분할이 (_PROBE_DAYS-14)일보다 오래 전에 발생했는데 그동안 유지보수가 연속
  스킵됐다면 옛 계수 날짜가 프로브 밖으로 밀려 감지 못 할 수 있다(정상 월간 실행에선 무해).
"""
from datetime import datetime, timezone

from batch.candles import backfill_stock_daily
from common.marketdata.toss_daily import fetch_daily
from common.clickhouse_client import create_client

_STALE_DAYS = 20                 # 최신 봉이 이보다 오래되면 refresh 누락으로 보고 전체 재백필
_TOL = 0.005                     # 종가 상대오차 허용(분할=배수차라 명확, 배당 재조정도 대부분 초과)
_PROBE_DAYS = 200                # 감지 조회 구간 — 토스 1페이지(200봉). refresh(14일) 밖 과거 재조정 노출 + 유지보수 스킵 내성


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
        return False                             # 이번 회차 skip(다음 회차 재시도) — 재조정은 full 없인 미해결
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
