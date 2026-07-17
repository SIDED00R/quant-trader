"""매매 잡 결과 → 텔레그램 알림 문안 (단일 책임: 순수 문자열 조립 — I/O 없음).

코인(trade_once)·KR/US 주식(stock_trade_once/us_trade_once)의 종료 상태를 사람이 읽을 한국어
메시지로 만든다. 매매했으면 주문 내역 전부, 안 했으면 '매매하지 않음'+사유가 반드시 들어간다.
"""
import os
from datetime import datetime
from traceback import extract_tb
from zoneinfo import ZoneInfo


def _header(market_label: str, dry_run: bool = False) -> str:
    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M")
    tag = " (DRY-RUN)" if dry_run else ""
    return f"[{market_label}]{tag} {now} KST"


def stock_message(market_label: str, r: dict, live: bool) -> str:
    """KR/US 주간 리밸런싱 결과 문안. r = execute() 반환 dict(bar/cash/targets/buys/sells/placed/skipped).

    KR placed 항목은 side(BUY|SELL)·accepted/msg/filled/filled_qty, US는 status/attempts 추가 —
    .get()으로 공용 처리(side 미지정=매수).
    """
    lines = [_header(market_label, dry_run=not live)]
    lines.append(f"bar={r.get('bar')} 타깃 {len(r.get('targets') or [])}종목 / "
                 f"매수후보 {len(r.get('buys') or [])} / 이탈 {len(r.get('sells') or [])}")
    if r.get("skipped"):
        lines.append(f"매매하지 않음 — {r['skipped']}")
        return "\n".join(lines)
    if not live:
        lines.append("계획만 산출(주문 없음)")
        return "\n".join(lines)
    if not r.get("targets"):
        lines.append("매매하지 않음 — 모델 실행됐으나 유효 종목 없음(주간 마커 미기록, 다음 평일 재시도)")
        return "\n".join(lines)
    placed = r.get("placed") or []
    if not placed:                       # 발주 자체가 없음(buys·발주 대상 sells 없음) = 이미 목표 보유
        lines.append("매매하지 않음 — 이미 목표 정렬(이번주 완료 기록)")
        return "\n".join(lines)
    accepted = sum(1 for o in placed if o.get("accepted"))
    filled = sum(1 for o in placed if o.get("filled"))
    for o in placed:
        sym, qty = o.get("symbol"), o.get("qty", 0)
        side = "매도" if o.get("side") == "SELL" else "매수"
        if o.get("error"):
            lines.append(f"· {side} {sym} {qty}주 — 오류: {o['error']}")
        elif not o.get("accepted"):
            lines.append(f"· {side} {sym} — 거부/스킵: {o.get('msg') or o.get('status') or '사유 미상'}")
        elif o.get("filled"):
            lines.append(f"· {side} {sym} {qty}주 — 체결 {o.get('filled_qty', 0)}주"
                         + (f" (시도 {o['attempts']}회)" if o.get("attempts") else ""))
        else:
            lines.append(f"· {side} {sym} {qty}주 — 접수(미체결)")
    lines.append(f"요약: 접수 {accepted}건 / 체결 {filled}건 · 주문 전 현금 {r.get('cash', 0):,.0f}")
    if filled == 0:
        lines.append("체결 0건 — 주간 마커 미기록, 다음 평일 재시도")
    return "\n".join(lines)


def ichimoku_message(market_label: str, r: dict, live: bool) -> str:
    """KR 일목 페이퍼 매매 결과 문안. stock_message와 달리 **마커를 체결 여부와 무관하게 기록**하는
    잡 전용 — "체결 0건→미기록·재시도" 오보를 피한다(실행 성공=그 주 완료). r=execute() 반환 dict.
    """
    lines = [_header(market_label, dry_run=not live)]
    if r.get("skipped"):
        lines.append(f"매매하지 않음 — {r['skipped']}")
        return "\n".join(lines)
    lines.append(f"bar={r.get('bar')} 진입후보 {len(r.get('targets') or [])} / "
                 f"매수 {len(r.get('buys') or [])} / 청산 {len(r.get('sells') or [])}")
    if not live:
        lines.append("계획만 산출(주문 없음)")
        return "\n".join(lines)
    placed = r.get("placed") or []
    if not placed:
        lines.append("매매 없음 — 신규 진입·청산 대상 없음(이번주 완료 기록)")
        return "\n".join(lines)
    filled = sum(1 for o in placed if o.get("filled"))
    for o in placed:
        side = "매도" if o.get("side") == "SELL" else "매수"
        mark = "체결" if o.get("filled") else f"미체결({o.get('msg')})"
        lines.append(f"· {side} {o.get('symbol')} {o.get('qty', 0)}주 — {mark}")
    lines.append(f"요약: 체결 {filled}/{len(placed)}건 · 이번주 완료 기록 · 주문 전 현금 {r.get('cash', 0):,.0f}")
    return "\n".join(lines)


def coin_message(decisions: list, rejected: int, targets: dict, dry_run: bool,
                 balances: dict | None = None) -> str:
    """코인 일일 매매 결과 문안. decisions 항목은 symbol/action/quantity/price/reason(+executed)."""
    lines = [_header("코인", dry_run=dry_run)]
    lines.append(f"목표비중 {targets} · 결정 {len(decisions)}건 기록")
    trades = [d for d in decisions if d.get("action") in ("BUY", "SELL")]
    if not trades:
        lines.append("매매하지 않음 — 전 종목 목표 유지(밴드 내)")
    else:
        for d in trades:
            state = "예정" if dry_run else ("체결" if d.get("executed") else "거부(잔고/보유 부족)")
            lines.append(f"· {d['action']} {d['symbol']} {d.get('quantity')} @ {float(d.get('price') or 0):,.0f} — {state}")
        if not dry_run:
            lines.append(f"요약: 매매 {len(trades)}건 / 체결 {len(trades) - rejected} / 거부 {rejected}")
    for acct, b in (balances or {}).items():
        lines.append(f"계좌 {acct}: 현금 {float(b.get('cash') or 0):,.0f} / 평가 {float(b.get('equity') or 0):,.0f}")
    return "\n".join(lines)


def error_message(market_label: str, exc: BaseException) -> str:
    """잡 예외 문안 — 어느 파일:라인(함수)에서 무슨 예외였는지 자동 표기."""
    lines = [_header(market_label), f"🔴 매매 잡 오류 — {type(exc).__name__}: {exc}"]
    tb = extract_tb(exc.__traceback__)
    if tb:
        last = tb[-1]
        lines.append(f"위치: {os.path.basename(last.filename)}:{last.lineno} ({last.name})")
    return "\n".join(lines)
