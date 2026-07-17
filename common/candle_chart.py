"""캔들차트 렌더 (단일 책임: OHLC[+일목 구름] → PNG bytes, pillow 순수 드로잉).

matplotlib 등 플로팅 의존 금지(레포 관례 — 이미지 비대·디스크 사고 예방). 이미지 내 텍스트는 ASCII만
(pillow 내장 폰트 한글 미지원 — 한글은 호출부 캡션이 담당, `common/equity_chart_telegram` 선례).
구름(선행스팬 A/B)은 반투명 RGBA 레이어로 그린 뒤 alpha_composite → 그 위 전환/기준선 → 캔들 순.
`lines`(common.ichimoku.ichimoku_lines 결과, bars와 동일 길이)가 있으면 오버레이, 없으면 캔들만.
"""
import io

W, H = 1000, 600
PAD_L, PAD_R, PAD_T, PAD_B = 16, 66, 44, 34
SURFACE, INK, GRID, MUTED = "#ffffff", "#1f2328", "#e7ebf0", "#59636e"
UP, DOWN = "#d64b4b", "#4062d8"                 # 국내 관례: 상승=적, 하락=청
TENKAN, KIJUN = "#0d9488", "#7c3aed"
CLOUD_UP, CLOUD_DN = (34, 197, 94, 70), (239, 68, 68, 70)   # spanA>=spanB 녹 / 반대 적(반투명)


def _ascii(s: str) -> str:
    """내장 비트맵 폰트는 비ASCII 글리프가 없어 깨진다 — 안전 치환(한글 제목은 캡션이 담당)."""
    return "".join(ch if ord(ch) < 128 else "?" for ch in str(s))


def render_candle_chart(bars: list, title: str, lines: dict | None = None) -> bytes:
    """bars=[(date,o,h,l,c[,v])] 오름차순(표시 구간으로 슬라이스된 상태) → PNG bytes.

    lines: {"tenkan","kijun","span_a","span_b"} — bars와 동일 인덱스(구름은 '그 시각에 그려진' 값). None이면 캔들만.
    """
    from PIL import Image, ImageDraw, ImageFont

    n = len(bars)
    if n < 1:
        raise ValueError("bars 비어 있음")
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    vals = list(highs) + list(lows)
    if lines:                                   # 구름·라인도 y범위에 포함(잘림 방지)
        for key in ("tenkan", "kijun", "span_a", "span_b"):
            vals += [v for v in lines.get(key, []) if v is not None]
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad

    plotw, ploth = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    x = lambda i: PAD_L + plotw * (0.5 if n == 1 else i / (n - 1))
    y = lambda p: PAD_T + ploth * (1.0 - (p - lo) / (hi - lo))
    cw = max(2.0, plotw / n * 0.6)

    img = Image.new("RGB", (W, H), SURFACE)
    d = ImageDraw.Draw(img)
    f_title = ImageFont.load_default(size=22)
    f_axis = ImageFont.load_default(size=14)

    # 가격 그리드 + 우측 라벨
    for g in range(5):
        p = lo + (hi - lo) * g / 4
        yy = y(p)
        d.line([PAD_L, yy, W - PAD_R, yy], fill=GRID, width=1)
        d.text((W - PAD_R + 4, yy - 7), _ascii(f"{p:,.0f}"), font=f_axis, fill=MUTED)
    # 날짜 라벨(하단 4개, ASCII)
    span_days = (bars[-1][0] - bars[0][0]).days if n > 1 else 0
    fmt = "%m/%d" if span_days <= 400 else "%y-%m"
    for g in range(4):
        i = round((n - 1) * g / 3)
        d.text((x(i) - 16, H - PAD_B + 8), _ascii(bars[i][0].strftime(fmt)), font=f_axis, fill=MUTED)

    # 구름(RGBA 오버레이) — 유효 세그먼트별 사다리꼴 채움
    if lines:
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        sa, sb = lines["span_a"], lines["span_b"]
        for i in range(n - 1):
            if None in (sa[i], sb[i], sa[i + 1], sb[i + 1]):
                continue
            quad = [(x(i), y(sa[i])), (x(i + 1), y(sa[i + 1])),
                    (x(i + 1), y(sb[i + 1])), (x(i), y(sb[i]))]
            od.polygon(quad, fill=CLOUD_UP if sa[i] >= sb[i] else CLOUD_DN)
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        d = ImageDraw.Draw(img)
        for key, col in (("tenkan", TENKAN), ("kijun", KIJUN)):
            seq = lines[key]
            pts = [(x(i), y(seq[i])) for i in range(n) if seq[i] is not None]
            if len(pts) > 1:
                d.line(pts, fill=col, width=2, joint="curve")

    # 캔들(심지 + 몸통)
    for i, b in enumerate(bars):
        o, h, l, c = b[1], b[2], b[3], b[4]
        col = UP if c >= o else DOWN
        xc = x(i)
        d.line([xc, y(h), xc, y(l)], fill=col, width=1)
        yo, yc = y(o), y(c)
        top, bot = min(yo, yc), max(yo, yc)
        if bot - top < 1:
            bot = top + 1
        d.rectangle([xc - cw / 2, top, xc + cw / 2, bot], fill=col)

    # 제목 + 범례(일목일 때)
    d.text((PAD_L, 12), _ascii(title), font=f_title, fill=INK)
    if lines:
        d.text((PAD_L, H - 16), "TENKAN(9)  KIJUN(26)  CLOUD(52,+26)", font=f_axis, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
