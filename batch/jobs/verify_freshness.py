"""데이터 신선도 점검 (단일 책임: ClickHouse 원본 테이블 행수·최신일자·지연일 점검, 읽기전용).

수집기가 조용히 실패(키 만료·SEC UA 차단 등)해 테이블이 낡아도, 가격 신선도 게이트(stock_candles_1d만
검사)는 이를 못 잡는다. 본 스크립트는 maintenance_once가 자동 갱신하는 테이블의 count()+max(date)+지연일을
한눈에 보여주고, 임계 초과 시 종료코드로 알린다. maintenance_once의 마지막 스텝으로 배선 →
임계(critical) 테이블이 비거나 크게 낡으면 유지보수 리포트가 🔴로 뜬다. 읽기전용(SELECT만).

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.jobs.verify_freshness [--strict] [--notify]
"""
import argparse
import sys
from datetime import date, datetime, timezone

from common.clickhouse_client import create_client

# (라벨, FROM식, 날짜컬럼, 허용지연일, critical) — critical 위반 시 종료코드 1(유지보수 🔴).
# **maintenance_once가 자동 갱신하는 테이블만** 감시한다(안 그러면 상시 위반으로 알람이 무력화됨).
# 제외: index_membership·index_changes·stock_delisting(비정기 이벤트 — 지연일 판정 무의미),
#   stock_meta(정적·날짜컬럼 없음). 그 외 수집 테이블은 전부 감시(krx/fred도 월간 배선됨).
# stock_short는 시장별 수집기가 달라(US=FINRA, KR=KRX) 시장 스코프로 각각 감시.
_CHECKS = [
    ("stock_candles_1d",       "stock_candles_1d",              "window_start", 7,   True),   # 매매 입력 — 가장 중요
    ("fundamentals_quarterly", "fundamentals_quarterly",        "filed_date",   150, True),   # EDGAR+DART 월간
    ("institutional_13f",      "institutional_13f",             "period_end",   210, False),  # 분기말+45d+월간수집
    ("stock_short(US)",        "stock_short WHERE market='US'", "date",         45,  False),  # FINRA 격주+월간수집
    ("stock_short(KR)",        "stock_short WHERE market='KR'", "date",         45,  False),  # KRX 월간(krx.py 증분)
    ("stock_investor_flow",    "stock_investor_flow",           "date",         45,  False),  # KRX 수급 월간
    ("stock_foreign_holding",  "stock_foreign_holding",         "date",         45,  False),  # KRX 외국인보유 월간
    ("macro_daily",            "macro_daily",                   "date",         45,  False),  # FRED 월간
    ("factor_returns_daily",   "factor_returns_daily",          "date",         45,  False),  # Ken French 발표 지연
    ("insider_transactions",   "insider_transactions",          "filed_date",   150, False),  # SEC 분기 데이터셋
    ("earnings_calendar",      "earnings_calendar",             "announce_date", 150, False),  # 8-K recent(월간 누적)
]


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def check(log=print) -> list:
    """각 테이블 점검 → [(label, rows, last_date, days_behind, threshold, critical, breached)]."""
    ch = create_client()
    today = datetime.now(timezone.utc).date()   # window_start 등이 UTC 정규화라 UTC 기준으로 통일
    out = []
    for label, from_expr, col, thr, critical in _CHECKS:
        try:
            r = ch.query(f"SELECT count(), max({col}) FROM {from_expr}").result_rows[0]
            rows = int(r[0])
            last = _as_date(r[1]) if rows else None
            behind = (today - last).days if last else None
            breached = rows == 0 or (behind is not None and behind > thr)
        except Exception as e:                      # 테이블 부재/쿼리 오류도 위반으로
            rows, last, behind, breached = -1, None, None, True
            log(f"[verify] {label}: 쿼리 실패 — {type(e).__name__}: {e}")
        out.append((label, rows, last, behind, thr, critical, breached))
    return out


def _format(results: list) -> str:
    lines = ["table                     rows        last         behind  thr  ok"]
    for t, rows, last, behind, thr, crit, br in results:
        mark = "🔴" if (br and crit) else ("🟡" if br else "✅")
        last_s = str(last) if last else "-"
        behind_s = str(behind) if behind is not None else "-"
        lines.append(f"{t:<25} {rows:>9,}  {last_s:<11}  {behind_s:>6}  {thr:>3}  {mark}")
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="ClickHouse 데이터 신선도 점검(읽기전용)")
    p.add_argument("--strict", action="store_true", help="warn(비critical) 위반도 실패로 취급")
    p.add_argument("--notify", action="store_true", help="요약을 텔레그램으로 전송")
    a = p.parse_args(argv)

    results = check()
    report = _format(results)
    print(report)

    crit_bad = [t for t, *_ , crit, br in results if br and crit]
    warn_bad = [t for t, *_ , crit, br in results if br and not crit]
    summary = f"critical 위반 {len(crit_bad)}: {crit_bad} / warn {len(warn_bad)}: {warn_bad}"
    print(f"[verify] {summary}")

    if a.notify and (crit_bad or warn_bad):     # 이상 있을 때만 통보(정상 실행은 무음 — 유지보수 요약이 ✅ 표기)
        from common import notify_telegram
        notify_telegram.send(f"⚠️ 데이터 신선도 이상\n{summary}\n```\n{report}\n```")

    failed = bool(crit_bad) or (a.strict and bool(warn_bad))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
