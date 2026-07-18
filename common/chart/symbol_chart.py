"""종목 1개 → 봉차트 PNG + 한글 캡션 (단일 책임: 렌더러 조립). 텔레그램 봇·데일리 푸시 공용.

KR=주봉 캔들 + 일목균형표 구름(9/26/52·선행26) · US(기타)=일봉 캔들(일목 없음).
일목 지표는 전체 이력으로 계산한 뒤 표시 구간만 슬라이스(롤링 윈도우 좌측 잘림 방지).
이미지 텍스트는 ASCII(캔들 렌더러 제약) — 한글 명칭·통화·신호는 캡션이 담당.
"""
from common.marketdata import ichimoku
from common.chart.candle_chart import render_candle_chart

KR_WEEKS, US_DAYS = 104, 130


def _cloud_state(sig: dict | None) -> str:
    if sig is None:
        return "판정불가(이력부족)"
    c = sig["close"]
    if c > sig["cloud_top"]:
        return "위(강세)"
    if c < sig["cloud_bot"]:
        return "아래(약세)"
    return "안(중립)"


def chart_for_symbol(daily: list, market: str, symbol: str, name: str | None = None) -> tuple[bytes, str]:
    """daily=[(date,o,h,l,c)] 오름차순 → (png_bytes, 한글 캡션). 봉 부족 시 ValueError."""
    label = name or symbol
    if market == "KR":
        wb = ichimoku.weekly_bars(daily)
        if len(wb) < 2:
            raise ValueError(f"{symbol}: 주봉 데이터 부족")
        lines_full = ichimoku.ichimoku_lines(wb)
        k = min(KR_WEEKS, len(wb))
        bars = wb[-k:]
        lines = {key: vals[-k:] for key, vals in lines_full.items()}
        png = render_candle_chart(bars, f"{symbol} weekly ichimoku(9/26/52)", lines=lines)
        sig = ichimoku.latest_signal(wb, exit_rule="cloud")
        cap = (f"📊 {label}({symbol}) 주봉 — 종가 ₩{bars[-1][4]:,.0f} ({bars[-1][0]}) · "
               f"일목 구름 {_cloud_state(sig)}")
        return png, cap

    if len(daily) < 2:
        raise ValueError(f"{symbol}: 일봉 데이터 부족")
    bars = [(d, o, h, l, c, 0) for d, o, h, l, c in daily[-US_DAYS:]]
    png = render_candle_chart(bars, f"{symbol} daily", lines=None)
    unit = "$" if market == "US" else ""
    cap = f"📊 {label}({symbol}) 일봉 — 종가 {unit}{bars[-1][4]:,.2f} ({bars[-1][0]})"
    return png, cap
