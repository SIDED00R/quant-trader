"""자산 시계열 조회·합성 (단일 책임: equity_snapshots → 시장별 시리즈 + 전체(KRW 환산) 합성).

/equity/history 라우트와 README 차트 렌더러(scripts/render_equity_chart.py)가 공유한다.
- 시장별 포인트: (snap_date, equity, cash) 오름차순. 코인은 계정 지정(대시보드=세션 계정) 또는
  전 계정 합(README — 스냅샷은 auto_trade 계정만 기록되므로 합=운용 총액).
- TOTAL(KRW) = COIN + KR + US×usdkrw(FRED macro_daily) — 날짜별 최신값 forward-fill 합산.
  데이터가 있는 모든 시장이 관측을 시작한 날부터만 산출(시장 합류 시점의 계단 착시 방지).
"""
import bisect
from datetime import datetime, timedelta, timezone

from common.clickhouse_client import create_client
from common.equity_snapshot import KIS_ACCOUNT  # noqa: F401 — 읽기측 재노출(조회 콜러가 쓰기 모듈을 몰라도 되게)


def _since(days: int):
    return datetime.now(timezone.utc).date() - timedelta(days=days)


def fetch_market_series(conn, market: str, account_id: str, days: int) -> list[tuple]:
    """단일 시장·계정 시계열 [(snap_date, equity, cash)] 오름차순 — PK가 그대로 커버."""
    rows = conn.execute(
        "SELECT snap_date, equity, cash FROM equity_snapshots "
        "WHERE market=%s AND account_id=%s AND snap_date>=%s ORDER BY snap_date",
        (market, account_id, _since(days))).fetchall()
    return [(r[0], float(r[1]), float(r[2]) if r[2] is not None else None) for r in rows]


def fetch_coin_series_total(conn, days: int) -> list[tuple]:
    """코인 전 계정 합 시계열 — README 차트용(계정 무관 운용 총액)."""
    rows = conn.execute(
        "SELECT snap_date, sum(equity), sum(cash) FROM equity_snapshots "
        "WHERE market='COIN' AND snap_date>=%s GROUP BY snap_date ORDER BY snap_date",
        (_since(days),)).fetchall()
    return [(r[0], float(r[1]), float(r[2]) if r[2] is not None else None) for r in rows]


def fetch_usdkrw(days: int) -> list[tuple]:
    """FRED USD/KRW [(date, rate)] 오름차순 — 주말/휴일 forward-fill용 여유분 포함 조회."""
    rows = create_client().query(
        "SELECT date, usdkrw FROM macro_daily FINAL WHERE usdkrw > 0 AND date >= {since:Date} ORDER BY date",
        parameters={"since": _since(days + 30)}).result_rows
    return [(r[0], float(r[1])) for r in rows]


def _fx_at(fx: list[tuple], fx_dates: list, d):
    """d 이전(포함) 최신 환율 — forward-fill. 첫 환율보다 이르면 None."""
    i = bisect.bisect_right(fx_dates, d) - 1
    return fx[i][1] if i >= 0 else None


def merge_total_krw(series: dict[str, list], fx: list[tuple]) -> list[tuple]:
    """시장별 시리즈 → 전체 자산(KRW) [(date, total)] 합성.

    데이터가 있는 시장만 참여, 참여 시장 전부가 관측을 시작한 날부터. 날짜별 시장 최신값
    forward-fill, US는 usdkrw 환산. US가 참여하는데 환율이 없으면 합산 불가 → 빈 리스트.
    """
    active = {m: pts for m, pts in series.items() if pts}
    if not active:
        return []
    if "US" in active and not fx:
        return []
    fx_dates = [d for d, _ in fx]
    start = max(pts[0][0] for pts in active.values())
    dates = sorted({p[0] for pts in active.values() for p in pts if p[0] >= start})
    idx = {m: 0 for m in active}
    last = {m: None for m in active}
    out = []
    for d in dates:
        for m, pts in active.items():
            i = idx[m]
            while i < len(pts) and pts[i][0] <= d:
                last[m] = pts[i][1]
                i += 1
            idx[m] = i
        total = 0.0
        for m in active:
            v = last[m]
            if v is None:
                total = None
                break
            if m == "US":
                rate = _fx_at(fx, fx_dates, d)
                if rate is None:      # 환율 시계열보다 이른 날 — 그 날만 생략
                    total = None
                    break
                v *= rate
            total += v
        if total is not None:
            out.append((d, total))
    return out


def normalize(points: list[tuple]) -> list[tuple]:
    """첫 포인트=100 지수화 [(date, index)] — 통화가 달라도 한 축에서 수익률 비교."""
    if not points:
        return []
    base = float(points[0][1])
    if base <= 0:
        return []
    return [(p[0], float(p[1]) / base * 100.0) for p in points]


ORDER = ["TOTAL", "COIN", "KR", "US"]


def chart_rows(markets: dict[str, list], fx: list[tuple]) -> list[dict]:
    """시장 시계열 → 차트 행(TOTAL 합성 + 정규화 + 수익률 + 마지막 원값). 포인트<2 시리즈 제외.

    SVG(scripts/render_equity_chart)·텔레그램 PNG(common/equity_chart_telegram) 렌더 공용.
    """
    merged = dict(markets)
    merged["TOTAL"] = merge_total_krw(markets, fx)
    rows = []
    for key in ORDER:
        pts = merged.get(key) or []
        idx = normalize(pts)
        if len(idx) < 2:
            continue
        rows.append({"key": key, "points": idx, "ret": idx[-1][1] - 100.0,
                     "last_value": float(pts[-1][1]), "currency": "USD" if key == "US" else "KRW"})
    return rows
