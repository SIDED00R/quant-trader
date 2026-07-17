"""일목균형표 지표 (단일 책임: 일봉 → 주봉 리샘플 + 일목 라인 + 최신 신호).

순수 파이썬(pandas 불요) — app 이미지(차트 봇)·batch 이미지(일목 페이퍼 매매)가 공용으로 쓴다.
주봉 = ISO 주(월~일) 그룹: open=첫 거래일, high=구간 max, low=구간 min, close=마지막 거래일, volume=합.
백테스트(pandas 'W-FRI')와 동치 — 국내/미국 일봉엔 주말 행이 없어 ISO주 그룹 == 금요일 종료 주봉.
표준값 전환9·기준26·선행B52·선행이동26. 구름(선행스팬 A/B)은 raw를 +26 뒤로 시프트해 '그 시각에
그려진' 값을 쓴다(과거 데이터로 계산 → 무룩어헤드). 두 선행스팬이 모두 유효할 때만 구름으로 인정한다.
"""


def _iso_key(d):
    y, w, _ = d.isocalendar()
    return (y, w)


def weekly_bars(daily, drop_week_of=None):
    """일봉 [(date, o, h, l, c[, v])] 오름차순 → 주봉 [(week_end_date, o, h, l, c, v)] 오름차순.

    drop_week_of(date)가 주어지고 그 날짜가 속한 ISO 주가 마지막이면 (미완성) 주봉을 제거한다 —
    진행 중인 주의 부분봉으로 신호를 내지 않기 위함(무룩어헤드).
    """
    groups = {}
    order = []
    for row in daily:
        k = _iso_key(row[0])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)
    drop_key = _iso_key(drop_week_of) if drop_week_of is not None else None
    out = []
    for k in order:
        if k == drop_key:
            continue
        rows = groups[k]
        out.append((
            rows[-1][0],                       # 주 마지막 거래일
            rows[0][1],                        # open = 첫날 시가
            max(r[2] for r in rows),           # high
            min(r[3] for r in rows),           # low
            rows[-1][4],                       # close = 마지막날 종가
            sum((r[5] if len(r) > 5 else 0) for r in rows),  # volume
        ))
    return out


def ichimoku_lines(bars, tenkan=9, kijun=26, senkou_b=52, disp=26):
    """주봉 → {"tenkan","kijun","span_a","span_b"} (bars 인덱스 정렬, 이력 부족 구간은 None).

    span_a/b[i] = raw[i-disp](선행 이동) — 시각 i에 그려진 구름은 i-disp 시점 데이터로 산출된다.
    """
    n = len(bars)
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]

    def _mid(win, i):
        if i + 1 < win:
            return None
        return (max(highs[i - win + 1:i + 1]) + min(lows[i - win + 1:i + 1])) / 2.0

    ten = [_mid(tenkan, i) for i in range(n)]
    kij = [_mid(kijun, i) for i in range(n)]
    a_raw = [((ten[i] + kij[i]) / 2.0 if ten[i] is not None and kij[i] is not None else None)
             for i in range(n)]
    b_raw = [_mid(senkou_b, i) for i in range(n)]
    span_a = [a_raw[i - disp] if i - disp >= 0 else None for i in range(n)]
    span_b = [b_raw[i - disp] if i - disp >= 0 else None for i in range(n)]
    return {"tenkan": ten, "kijun": kij, "span_a": span_a, "span_b": span_b}


def latest_signal(bars, exit_rule="tk_cross", tenkan=9, kijun=26, senkou_b=52, disp=26):
    """마지막(완결) 주봉의 신호. 구름·전환·기준 중 어느 하나라도 미정의면 None.

    반환 {"close","cloud_top","cloud_bot","tenkan","kijun","entry","exit","breakout_pct"}.
    entry: 종가>구름상단 AND 전환>기준 · exit: tk_cross→전환<기준 / cloud→종가<=구름상단.
    """
    n = len(bars)
    if n < 2:
        return None
    lines = ichimoku_lines(bars, tenkan, kijun, senkou_b, disp)
    i = n - 1
    sa, sb = lines["span_a"][i], lines["span_b"][i]
    ten, kij = lines["tenkan"][i], lines["kijun"][i]
    if sa is None or sb is None or ten is None or kij is None:
        return None
    close = bars[i][4]
    cloud_top = max(sa, sb)
    cloud_bot = min(sa, sb)
    exit_ = (ten < kij) if exit_rule == "tk_cross" else (close <= cloud_top)
    return {
        "close": close, "cloud_top": cloud_top, "cloud_bot": cloud_bot,
        "tenkan": ten, "kijun": kij,
        "entry": bool(close > cloud_top and ten > kij),
        "exit": bool(exit_),
        "breakout_pct": (close - cloud_top) / cloud_top if cloud_top else 0.0,
    }
