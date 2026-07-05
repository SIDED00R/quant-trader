"""팩터 일간 수익률 적재 (단일 책임: Ken French Data Library → factor_returns_daily).

Fama-French 5팩터(Mkt-RF·SMB·HML·RMW·CMA·RF) + 모멘텀(UMD) 일간 시계열.
용도: 수익 귀인(내 초과수익이 알파인지 팩터노출인지) + 팩터중립 피처. 키 불필요(공개 zip).
원본 CSV는 %표기 → /100(소수)로 저장. 결측 코드(-99.99/-999)는 건너뜀. 재실행 멱등(ReplacingMergeTree).
KR 전용 팩터 파일은 라이브러리에 없음 — region 컬럼으로 추후 자체구성 확장 여지만 남긴다.

실행: PYTHONPATH=. .venv/Scripts/python.exe -m batch.data.factor_returns
"""
import argparse
import io
import sys
import zipfile
from datetime import date

import httpx

from common.clickhouse_client import create_client
from common.constants import SEC_USER_AGENT

_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
_FILES = [
    f"{_BASE}/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
    f"{_BASE}/F-F_Momentum_Factor_daily_CSV.zip",
]
# 원본 CSV 컬럼명 → 우리 factor 코드
_FACTOR_MAP = {"Mkt-RF": "mkt_rf", "SMB": "smb", "HML": "hml", "RMW": "rmw",
               "CMA": "cma", "RF": "rf", "Mom": "umd"}
_MISSING = -99.0            # Ken French 결측 sentinel(-99.99 / -999) 하한
_COLS = ["date", "region", "factor", "ret"]


def _download_csv(url: str, client: httpx.Client) -> str:
    """공개 zip 다운로드 → 내부 단일 .CSV 텍스트 반환."""
    r = client.get(url)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    name = next((n for n in zf.namelist() if n.upper().endswith(".CSV")), None)
    if name is None:
        raise RuntimeError(f"[factor] zip 내 CSV 없음 — 포맷 변경 의심: {url}")
    return zf.read(name).decode("latin1")


def _parse(csv_text: str) -> list:
    """설명 헤더 스킵 → 컬럼헤더 탐지 → 8자리 날짜 행만 파싱. [(date, factor, ret_decimal)]."""
    cols: list | None = None
    out: list = []
    for raw in csv_text.splitlines():
        line = raw.strip()
        if not line:
            if cols:                                   # 데이터부 종료(연간 섹션/저작권 앞 공백행)
                break
            continue
        parts = [p.strip() for p in line.split(",")]
        head = parts[0]
        if cols is None:
            # 컬럼헤더 = 첫 칸 비어있고 이후 칸에 알려진 팩터명 포함
            if head == "" and any(p in _FACTOR_MAP for p in parts[1:]):
                cols = parts[1:]
            continue
        if not (len(head) == 8 and head.isdigit()):    # 데이터부 벗어남(연간/저작권) → 종료
            break
        d = date(int(head[:4]), int(head[4:6]), int(head[6:]))
        for name, val in zip(cols, parts[1:]):
            factor = _FACTOR_MAP.get(name)
            if not factor or val == "":
                continue
            try:
                v = float(val)
            except ValueError:
                continue
            if v <= _MISSING:                          # 결측 코드
                continue
            out.append((d, factor, v / 100.0))
    return out


def collect(log=print) -> int:
    rows: list = []
    with httpx.Client(timeout=60, headers={"User-Agent": SEC_USER_AGENT},
                      follow_redirects=True) as c:
        for url in _FILES:
            csv_text = _download_csv(url, c)
            parsed = _parse(csv_text)
            rows += [[d, "US", f, r] for d, f, r in parsed]
            log(f"[factor] {url.split('/')[-1]}: {len(parsed):,}행")
    if not rows:
        raise RuntimeError("[factor] 파싱 결과 0행 — Ken French 포맷 변경 의심")
    ch = create_client()
    ch.insert("factor_returns_daily", rows, column_names=_COLS)
    dmin = min(r[0] for r in rows); dmax = max(r[0] for r in rows)
    log(f"[factor] 완료: {len(rows):,}행 ({dmin}~{dmax}) → factor_returns_daily")
    return len(rows)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argparse.ArgumentParser(description="Ken French 팩터 → factor_returns_daily").parse_args(argv)
    collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
