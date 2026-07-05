"""정기 데이터 유지보수 1회 실행 (단일 책임: 월간 선별 재백필 + 분기/월간 데이터 수집기 재실행 + 신선도 점검).

매매 전 증분 갱신(refresh_stock_daily, days=14)이 다루지 못하는 세 가지를 보수한다:
① 수정주가 기준 통일 — 토스 adjusted 시계열은 요청 시점 기준 전체 재조정이라, 분할 발생 종목은
   증분 14일 경계에서 조정계수가 갈라진다 → 재조정 감지된 종목만 전체 재백필해 기준을 다시 맞춘다(selective_stock_backfill).
② 분기/월간 공시·데이터 반영 — EDGAR 펀더멘털·13F·SIC 섹터·DART 펀더멘털 + 팩터(Ken French)·
   내부자거래(SEC Form 4)·US 공매도(FINRA)·실적발표일(SEC 8-K) 수집기 재실행(전부 멱등·증분).
   + 연구 데이터 지속 수집(모델 미사용이어도 재사용 자산으로 축적): KRX 수급·공매도·외국인보유(증분)·
   FRED 매크로·KR 상폐 메타·KR/US 지수 PIT 멤버십 — 이들 수집기는 **스텝 내부에서 지연 import**한다
   (KRX 계열은 pykrx가 import 시점에 KRX 로그인을 수행, 나머지는 무거운 의존 격리·일관성 목적 —
   다른 스텝·CI import 스윕이 로그인/의존에 묶이지 않게).
③ 데이터 신선도 점검 — 마지막에 verify_freshness로 임계 테이블 낡음/빔을 리포트에 🔴로 노출.
단계별 격리 — 한 단계 실패가 나머지를 막지 않고, 결과는 텔레그램으로 통보한다.
Cloud Scheduler(trade-vm-maintenance)가 매월 첫 토요일 04:00 UTC에 매매 VM을 기동해 실행한다.
"""
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from batch.backtest import selective_stock_backfill
from batch.backtest.refresh_stock_daily import alive_symbols
from batch.data import (earnings, factor_returns, finra_short, fundamentals,
                        insider, kr_fundamentals, sec_13f, sec_sector,
                        verify_freshness)
from common import notify_telegram


def _krx_flow_step() -> int:
    from batch.data import krx                      # 지연 import — pykrx가 import 시 KRX 로그인
    return krx.main([])                             # --start 미지정 = 증분(적재 최신일−7일)


def _kr_membership_step() -> int:
    from batch.data import kr_index_membership      # 지연 import — 위와 동일
    return kr_index_membership.main([])             # 월별 그리드 전체 재도출(저렴·멱등)


def _fred_step() -> int:
    from batch.data import fred                     # 지연 import — 무거운 의존 격리(일관성)
    return fred.main([])                            # 소량(9시리즈)이라 전량 재수집·멱등


def _kr_delisted_step() -> int:
    from batch.data import kr_delisted              # 지연 import — FDR
    return kr_delisted.main(["--no-prices"])        # 메타만(상폐 OHLCV 백필은 수동 1회성)


def _us_membership_step() -> int:
    from batch.data import us_membership          # 지연 import — 일관성(경량·키리스)
    return us_membership.main([])



_UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "backtest" / "universe"
_DAYS = 2600   # 풀 재백필 기간 — 연구·초기 시딩과 동일(~7.1년)


def _universe() -> list:
    """유니버스 파일 합집합 + 테이블 활성 종목(중복 제거, 순서 보존) — 신규 편입·기존 활성 모두 포함."""
    seen: dict = {}
    for f in sorted(_UNIVERSE_DIR.glob("*.txt")):
        for s in f.read_text(encoding="utf-8").replace("\n", ",").split(","):
            if s.strip():
                seen.setdefault(s.strip(), None)
    for mk in ("KR", "US"):
        for s in alive_symbols(mk):
            seen.setdefault(s, None)
    return list(seen)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    year = datetime.now(timezone.utc).year
    syms = _universe()
    steps = [
        ("일봉 선별 재백필", lambda: selective_stock_backfill.run(syms, _DAYS)),
        ("EDGAR 펀더멘털", lambda: fundamentals.main(["--fetch"])),
        ("13F 기관보유", lambda: sec_13f.main(["--start-year", str(year)])),
        ("SIC 섹터", lambda: sec_sector.main([])),
        ("DART 펀더멘털", lambda: kr_fundamentals.main(["--start-year", str(year), "--symbols", ",".join(alive_symbols("KR"))])),
        ("팩터 수익률", lambda: factor_returns.main([])),
        ("내부자 거래", lambda: insider.main(["--start-year", str(year)])),
        ("US 공매도", lambda: finra_short.main([])),
        ("실적 캘린더", lambda: earnings.main([])),
        # ── 연구 데이터 지속 수집(모델 미사용, 재사용 자산 축적 — 사용자 결정 2026-07-05) ──
        ("KR 수급·공매도(KRX)", _krx_flow_step),
        ("KR 지수 PIT 멤버십", _kr_membership_step),
        ("FRED 매크로", _fred_step),
        ("KR 상폐 메타", _kr_delisted_step),
        ("US 지수 PIT 멤버십", _us_membership_step),
        ("데이터 신선도 점검", lambda: verify_freshness.main(["--notify"])),   # 마지막 — 임계 낡음/빔 → 🔴 + 이상 시 상세 텔레그램
    ]
    lines, failed = [], 0
    for name, fn in steps:                      # 단계별 격리 — 실패해도 다음 단계 진행
        try:
            rc = fn()
            ok = rc in (0, None)
            failed += 0 if ok else 1
            lines.append(f"{'✅' if ok else '🔴'} {name}" + ("" if ok else f" (exit={rc})"))
        except Exception as e:
            traceback.print_exc()
            failed += 1
            lines.append(f"🔴 {name}: {type(e).__name__}: {e}")
    now = datetime.now(timezone.utc).strftime("%m-%d %H:%M UTC")
    sent = notify_telegram.send(f"[데이터 유지보수] {now}\n대상 {len(syms)}종목\n" + "\n".join(lines))
    if failed:
        return 70 if sent else 1                # 70=텔레그램 통보 완료(startup notify_fail 스킵)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
