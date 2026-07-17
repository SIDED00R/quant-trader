"""README 자산 차트 렌더 (단일 책임: equity_snapshots → 통합 수익률 SVG 라이트/다크 2벌).

stdlib만으로 SVG를 문자열 조립한다(matplotlib 등 플로팅 의존 도입 금지 — 이미지 비대·디스크 사고 예방).
시리즈 = 전체(KRW 환산)+코인+국장+미장 — 전 시장 공통 시작일=0% 리베이스(수익 +/손실 −, 통화 상이 해소), 범례에 현재값·수익률%.
데이터가 없어도 placeholder SVG를 항상 출력한다(README 깨진 이미지 방지). 매매 VM startup이
잡 종료 후 실행해 charts/*.svg 생성 → assets 브랜치 발행(infra/trade-vm-startup.sh).

실행: python -m scripts.render_equity_chart [--out charts] [--days 365]
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from common.equity_series import (
    ICHIMOKU_ACCOUNT,
    KIS_ACCOUNT,
    chart_rows,
    fetch_coin_series_total,
    fetch_market_series,
    fetch_usdkrw,
)
from common.postgres_client import close_pool, open_pool, pool

W, H = 920, 420
PAD_L, PAD_R, PAD_T, PAD_B = 52, 20, 96, 42
FONT = "-apple-system,'Segoe UI','Malgun Gothic',sans-serif"
LABELS = {"TOTAL": "전체(KRW)", "COIN": "코인", "KR": "국장", "KR_ICHIMOKU": "국장 일목", "US": "미장"}
WIDTHS = {"TOTAL": 3.0, "COIN": 2.0, "KR": 2.0, "KR_ICHIMOKU": 2.0, "US": 2.0}
DASHES = {"KR_ICHIMOKU": "6 4"}   # 페이퍼(가상) 시리즈는 점선 — 국장(실계좌) 실선과 구분
# 시리즈 색 — 대시보드(index.html --coin/--kr/--us)와 동일 세트(CVD 검증 통과). 전체=잉크 굵은 선. 국장 일목=핑크.
THEMES = {
    "light": {"surface": "#ffffff", "ink": "#1f2328", "muted": "#59636e", "grid": "#e7ebf0",
              "series": {"TOTAL": "#1f2328", "COIN": "#0d9488", "KR": "#4a3aa7", "KR_ICHIMOKU": "#bf3989", "US": "#eb6834"}},
    "dark": {"surface": "#0d1117", "ink": "#e6edf3", "muted": "#8d96a0", "grid": "#262c33",
             "series": {"TOTAL": "#e6edf3", "COIN": "#0d9488", "KR": "#9085e9", "KR_ICHIMOKU": "#db61a2", "US": "#d95926"}},
}


def _money(currency: str, v: float) -> str:
    return ("$" if currency == "USD" else "₩") + f"{v:,.0f}"


def _tw(s: str, size: float = 12.0) -> float:
    """근사 텍스트 폭(px) — 한글/전각 ≈1.05em, ASCII ≈0.58em (범례 배치용)."""
    return sum(size * (1.05 if ord(ch) > 0x2E80 else 0.58) for ch in s)


def prepare_rows(markets: dict[str, list], fx: list[tuple]) -> list[dict]:
    """공용 chart_rows + SVG 범례용 현재값 텍스트 — 순수(테스트 대상)."""
    rows = chart_rows(markets, fx)
    for r in rows:
        r["value_text"] = _money(r["currency"], r["last_value"])
    return rows


def _polyline(points, x, y) -> str:
    return " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in points)


def build_svg(rows: list[dict], theme: dict, updated: str) -> str:
    """통합 수익률 라인차트 SVG — rows 비면 placeholder(파일은 항상 유효한 차트 프레임)."""
    s = theme["series"]
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="{FONT}">'
        f'<rect width="{W}" height="{H}" rx="12" fill="{theme["surface"]}"/>'
        f'<text x="{PAD_L}" y="34" font-size="17" font-weight="700" fill="{theme["ink"]}">시장별 자산 추이</text>'
        f'<text x="{PAD_L}" y="54" font-size="11.5" fill="{theme["muted"]}">'
        f'공통 시작일=0% 수익률(+수익/−손실) · 코인/국장/미장 + 전체(달러는 FRED USD/KRW 환산) · 매매 잡 종료 시 자동 갱신 — {updated}</text>'
    )
    if not rows:
        return (head + f'<text x="{W / 2}" y="{H / 2 + 14}" font-size="14" fill="{theme["muted"]}" '
                       f'text-anchor="middle">데이터 수집 중 — 첫 매매 잡 이후 자산 곡선이 생성됩니다</text></svg>')

    # 범례(스와치=시리즈 색, 텍스트=잉크/뮤트 — 텍스트에 데이터색 금지)
    legend, lx = [], PAD_L
    for r in rows:
        ret = f"{r['ret']:+.1f}%"
        label = LABELS[r["key"]]
        detail = f'{r["value_text"]} {ret}'
        legend.append(
            f'<rect x="{lx}" y="70" width="16" height="4" rx="2" fill="{s[r["key"]]}"/>'
            f'<text x="{lx + 22:.0f}" y="76" font-size="12" font-weight="600" fill="{theme["ink"]}">{label}</text>'
            f'<text x="{lx + 22 + _tw(label) + 7:.0f}" y="76" font-size="12" fill="{theme["muted"]}">{detail}</text>')
        lx += 22 + _tw(label) + 7 + _tw(detail) + 26

    ds = [d for r in rows for d, _ in r["points"]]
    vs = [v for r in rows for _, v in r["points"]]
    d0, d1 = min(ds).toordinal(), max(ds).toordinal()
    v0, v1 = min(vs), max(vs)
    pad = max((v1 - v0) * 0.08, 0.5)
    v0, v1 = v0 - pad, v1 + pad
    x = lambda d: PAD_L + (W - PAD_L - PAD_R) * (0.5 if d1 == d0 else (d.toordinal() - d0) / (d1 - d0))
    y = lambda v: (H - PAD_B) - (H - PAD_B - PAD_T) * (v - v0) / (v1 - v0)

    grid = []
    for g in range(4):
        v = v0 + (v1 - v0) * g / 3
        yy = y(v)
        grid.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W - PAD_R}" y2="{yy:.1f}" '
                    f'stroke="{theme["grid"]}" stroke-width="1"/>'
                    f'<text x="{PAD_L - 8}" y="{yy - 3:.1f}" font-size="10.5" fill="{theme["muted"]}" '
                    f'text-anchor="end">{v:+.1f}%</text>')
    # 0% 기준선(손익 경계) — 모든 시리즈의 출발점이라 항상 범위 안에 있다(앵커 0%).
    if v0 <= 0.0 <= v1:
        y0 = y(0.0)
        grid.append(f'<line x1="{PAD_L}" y1="{y0:.1f}" x2="{W - PAD_R}" y2="{y0:.1f}" '
                    f'stroke="{theme["muted"]}" stroke-width="1" stroke-dasharray="4 3"/>')
    span_days = d1 - d0
    fmt = "%m/%d" if span_days <= 200 else "%Y-%m"
    dates_sorted = sorted(set(ds))
    for g in range(4):
        target = d0 + span_days * g / 3
        d = min(dates_sorted, key=lambda dd: abs(dd.toordinal() - target))
        grid.append(f'<text x="{x(d):.1f}" y="{H - 16}" font-size="10.5" fill="{theme["muted"]}" '
                    f'text-anchor="middle">{d.strftime(fmt)}</text>')

    lines = []
    for r in rows:
        color, width = s[r["key"]], WIDTHS[r["key"]]
        dash = f' stroke-dasharray="{DASHES[r["key"]]}"' if r["key"] in DASHES else ""
        lines.append(f'<polyline points="{_polyline(r["points"], x, y)}" fill="none" stroke="{color}" '
                     f'stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        ed, ev = r["points"][-1]
        lines.append(f'<circle cx="{x(ed):.1f}" cy="{y(ev):.1f}" r="6" fill="{theme["surface"]}"/>'
                     f'<circle cx="{x(ed):.1f}" cy="{y(ev):.1f}" r="4" fill="{color}"/>')

    return head + "".join(legend) + "".join(grid) + "".join(lines) + "</svg>"


def load_markets(days: int) -> tuple[dict, list]:
    """DB 조회 — 코인=전 계정 합(운용 총액), KR/US=단일 KIS 계좌, 환율 실패는 TOTAL만 생략."""
    open_pool()
    try:
        with pool.connection() as conn:
            markets = {
                "COIN": fetch_coin_series_total(conn, days),
                "KR": fetch_market_series(conn, "KR", KIS_ACCOUNT, days),
                "KR_ICHIMOKU": fetch_market_series(conn, "KR", ICHIMOKU_ACCOUNT, days),
                "US": fetch_market_series(conn, "US", KIS_ACCOUNT, days),
            }
    finally:
        close_pool()
    try:
        fx = fetch_usdkrw(days)
    except Exception as e:
        print(f"[equity-chart] 환율 조회 실패 — 전체(KRW) 시리즈 생략: {type(e).__name__}: {e}")
        fx = []
    return markets, fx


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="equity_snapshots → README 자산 차트 SVG(라이트/다크)")
    ap.add_argument("--out", default="charts", help="출력 디렉터리")
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args(argv)
    markets, fx = load_markets(a.days)
    rows = prepare_rows(markets, fx)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for mode, theme in THEMES.items():
        path = out / f"equity-{mode}.svg"
        path.write_text(build_svg(rows, theme, updated), encoding="utf-8")
        print(f"[equity-chart] {path} ({'placeholder' if not rows else f'{len(rows)}시리즈'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
