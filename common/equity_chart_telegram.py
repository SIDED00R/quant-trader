"""자산 차트 텔레그램 발송 (단일 책임: equity_snapshots → PNG 렌더 → 텔레그램 사진 1장).

코인 데일리 잡(trade_once) 종료 훅 — 하루 1장(스위퍼 재실행은 trade_once의 '이미 실행됨' 가드가 차단).
VM·대시보드 없이 텔레그램에서 자산 흐름을 보게 하는 용도(README assets 차트의 푸시판).
모든 실패를 삼키고 False 반환 — 매매 결과·기존 텍스트 알림에 절대 영향 없음.
이미지 내 라벨은 ASCII(TOTAL/COIN/KR/US — pillow 내장 폰트가 한글 미지원), 한글 요약은 캡션이 담당.
pillow는 지연 import(미설치 환경에서도 모듈 import 안전 — notify_telegram의 telethon과 동일 방침).

수동 실행: python -m common.equity_chart_telegram [--days 365]
"""
import argparse
import io
import sys
from datetime import datetime, timezone

from common import notify_telegram
from common.equity_series import (
    KIS_ACCOUNT,
    chart_rows,
    fetch_coin_series_total,
    fetch_market_series,
    fetch_usdkrw,
)
from common.postgres_client import open_pool, pool

W, H = 1200, 560
PAD_L, PAD_R, PAD_T, PAD_B = 78, 30, 112, 58
INK, MUTED, GRID, SURFACE = "#1f2328", "#59636e", "#e7ebf0", "#ffffff"
SERIES = {"TOTAL": "#1f2328", "COIN": "#0d9488", "KR": "#4a3aa7", "US": "#eb6834"}  # README 라이트 세트와 동일
WIDTHS = {"TOTAL": 5, "COIN": 3, "KR": 3, "US": 3}
LABELS_KO = {"TOTAL": "전체", "COIN": "코인", "KR": "국장", "US": "미장"}


def _money(currency: str, v: float) -> str:
    return ("$" if currency == "USD" else "₩") + f"{v:,.0f}"


def _load_rows(days: int) -> list[dict]:
    """스냅샷·환율 조회 → 공용 차트 행. pool은 닫지 않는다(trade_once 실행 중 훅 — 수명 소유는 콜러)."""
    open_pool()   # 멱등 — CLI 단독 실행도 지원
    with pool.connection() as conn:
        markets = {
            "COIN": fetch_coin_series_total(conn, days),
            "KR": fetch_market_series(conn, "KR", KIS_ACCOUNT, days),
            "US": fetch_market_series(conn, "US", KIS_ACCOUNT, days),
        }
    try:
        fx = fetch_usdkrw(days)
    except Exception as e:
        print(f"[equity-tg] 환율 조회 실패 — 전체(KRW) 시리즈 생략: {type(e).__name__}: {e}")
        fx = []
    return chart_rows(markets, fx)


def _caption(rows: list[dict], now=None) -> str:
    """한글 요약 캡션 — 이미지 라벨이 ASCII라 한글 명칭·통화 상세는 여기서 전달."""
    now = now or datetime.now(timezone.utc)
    head = f"📈 자산 추이 — 기준일=100 ({now.strftime('%Y-%m-%d %H:%M')} UTC)"
    parts = [f"{LABELS_KO[r['key']]} {_money(r['currency'], r['last_value'])} ({r['ret']:+.1f}%)"
             for r in rows]
    return head + "\n" + " · ".join(parts)


def _render_png(rows: list[dict]) -> bytes:
    """공용 차트 행 → PNG(라이트 테마) — README SVG와 동일한 정규화 멀티라인 차트."""
    from PIL import Image, ImageDraw, ImageFont   # 지연 import — 모듈 도킹스트링 참조

    img = Image.new("RGB", (W, H), SURFACE)
    d = ImageDraw.Draw(img)
    f_title = ImageFont.load_default(size=26)
    f_leg = ImageFont.load_default(size=19)
    f_axis = ImageFont.load_default(size=16)
    d.text((PAD_L, 24), "quant-trader | equity index (base=100)", font=f_title, fill=INK)   # ASCII만(내장 폰트 글리프 한계)
    lx = PAD_L
    for r in rows:
        d.rectangle([lx, 76, lx + 26, 82], fill=SERIES[r["key"]])
        label = f"{r['key']} {r['ret']:+.1f}%"
        d.text((lx + 34, 68), label, font=f_leg, fill=INK)
        lx += 34 + int(d.textlength(label, font=f_leg)) + 30
    ds = [p[0] for r in rows for p in r["points"]]
    vs = [p[1] for r in rows for p in r["points"]]
    d0, d1 = min(ds).toordinal(), max(ds).toordinal()
    v0, v1 = min(vs), max(vs)
    pad = max((v1 - v0) * 0.08, 0.5)
    v0, v1 = v0 - pad, v1 + pad
    x = lambda dd: PAD_L + (W - PAD_L - PAD_R) * (0.5 if d1 == d0 else (dd.toordinal() - d0) / (d1 - d0))
    y = lambda v: (H - PAD_B) - (H - PAD_B - PAD_T) * (v - v0) / (v1 - v0)
    for g in range(4):
        v = v0 + (v1 - v0) * g / 3
        yy = y(v)
        d.line([PAD_L, yy, W - PAD_R, yy], fill=GRID, width=1)
        d.text((PAD_L - 10, yy - 9), f"{v:.1f}", font=f_axis, fill=MUTED, anchor="ra")
    dates_sorted = sorted(set(ds))
    for g in range(4):
        target = d0 + (d1 - d0) * g / 3
        dd = min(dates_sorted, key=lambda z: abs(z.toordinal() - target))
        d.text((x(dd), H - 36), dd.strftime("%m/%d"), font=f_axis, fill=MUTED, anchor="ma")
    for r in rows:
        pts = [(x(p[0]), y(p[1])) for p in r["points"]]
        d.line(pts, fill=SERIES[r["key"]], width=WIDTHS[r["key"]], joint="curve")
        ex, ey = pts[-1]                                     # 끝점 마커 + 서피스 링
        d.ellipse([ex - 9, ey - 9, ex + 9, ey + 9], fill=SURFACE)
        d.ellipse([ex - 6, ey - 6, ex + 6, ey + 6], fill=SERIES[r["key"]])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def send_chart(days: int = 365) -> bool:
    """자산 차트 PNG 1장 + 한글 캡션 전송. 데이터 미축적·렌더/전송 실패 전부 내부 흡수(False)."""
    try:
        rows = _load_rows(days)
        if not rows:
            print("[equity-tg] 시계열 미축적(시리즈당 2일 필요) — 발송 생략")
            return False
        ok = notify_telegram.send_photo(_render_png(rows), _caption(rows))
        print(f"[equity-tg] 자산 차트 발송 {'성공' if ok else '실패(비치명)'}")
        return ok
    except Exception as e:
        print(f"[equity-tg] 실패(비치명 — 매매 결과 무관): {type(e).__name__}: {e}")
        return False


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="자산 차트 PNG 텔레그램 발송")
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args(argv)
    return 0 if send_chart(a.days) else 2


if __name__ == "__main__":
    raise SystemExit(main())
