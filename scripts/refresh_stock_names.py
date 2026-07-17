"""종목명 번들 재생성 (단일 책임: FDR → common/refdata/stock_names.json 갱신).

로컬 dev 환경 전용(프로덕션 이미지 실행 경로 아님). KRX/SEC 실시간 엔드포인트가 불안정해 종목명은
repo에 정적 번들로 두고, 상장 변화가 느리므로 가끔만 재생성한다. FinanceDataReader가 필요하다(dev 의존):
    pip install finance-datareader
    python -m scripts.refresh_stock_names
"""
import json
import os

import FinanceDataReader as fdr

OUT = os.path.join(os.path.dirname(__file__), os.pardir, "common", "refdata", "stock_names.json")


def build() -> dict:
    out = {"KR": [], "US": []}
    kr = fdr.StockListing("KRX")
    for r in kr[["Code", "Name"]].itertuples(index=False):
        code, nm = str(r.Code).strip(), str(r.Name).strip()
        if len(code) == 6 and code.isdigit() and nm and nm != "nan":
            out["KR"].append([code, nm])
    seen = set()
    for mkt in ("NASDAQ", "NYSE", "AMEX"):
        df = fdr.StockListing(mkt)
        col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        for r in df[[col, "Name"]].itertuples(index=False):
            t, nm = str(getattr(r, col)).strip().upper(), str(r.Name).strip()
            if t and nm and nm != "nan" and t not in seen:
                seen.add(t)
                out["US"].append([t, nm])
    return out


def main() -> int:
    data = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[refresh-stock-names] KR {len(data['KR'])} · US {len(data['US'])} → {os.path.relpath(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
