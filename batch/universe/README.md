# 주식 유니버스 구성종목 리스트

백테스트/연구용 종목 유니버스(쉼표 구분, 1줄). `backfill_stock_daily.py --symbols-file` 입력.

| 파일 | 지수 | 종목수 | 출처 |
|---|---|---|---|
| `kospi200.txt` | KOSPI 200 (KR 6자리) | 199 | Naver Finance (KRX API는 WAF 차단) |
| `kosdaq150.txt` | KOSDAQ 150 (KR 6자리) | 150 | investing.com |
| `sp500.txt` | S&P 500 (US 티커) | 503 | Wikipedia + datasets CSV |
| `nasdaq100.txt` | NASDAQ-100 (US 티커) | 101 | Wikipedia |

- **스냅샷 일자: 2026-06-27 (현재 구성)**. KR 합 349(중복 0), US 합집합 515(NASDAQ-100 중 89개가 S&P500와 중복 → 12개만 고유). 전체 고유 864.
- **⚠ 생존편향**: 현재 구성종목이라 과거 백테스트에 소급하면 탈락·상폐 종목 누락 + 최근 편입 승자의 멤버십 룩어헤드로 수익이 **과대**된다. point-in-time(시점별) 구성 이력이 필요하나 무료 소스가 없다 — 결과 해석 시 보수적 차감(`docs/intraday_baseline.md` §6).
- `0009K0`(에이임드바이오)는 영숫자 KOSDAQ 단축코드(정상).
- **백필 적재 856/864** — 8종목 실패: 닷티커 `BRK.B`/`BF.B`(토스 미취급) 외 6종목(토스 커버리지 공백, KR 5·US 3 분포). 데이터 누락이라 결과 영향 미미.
