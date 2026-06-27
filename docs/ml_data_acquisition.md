# OHLCV 외 추가 변수 확보 계획 — KR(344) + US(512), 7.1년

> 외국인/기관 수급·지수 편입편출·공매도·펀더멘털·매크로·감성 등 **OHLCV 외 변수**를 우리 856종목에 실제 확보할 수 있는지 출처/API/난이도/우선순위로 평가한다(6개 도메인, 56개 변수 조사). 피처 정의는 [ml_features_research.md](ml_features_research.md).

## 0. 정직한 전제 (먼저 읽을 것)

- **데이터를 늘려도 3대 장벽은 남는다**: (1) **생존편향** — 현재 856종목만 보유. PIT 멤버십 마스크를 붙여도 "탈락/상폐 종목 가격 부재"라는 본체는 남는다(완전 제거는 별도 적재 과제). (2) **다중검정** — 변수↑ → 우연히 좋아 보이는 피처↑. 모든 신규 피처는 purged+embargo CV + IC/ICIR로 측정하고 다중검정 보정 없이는 채택 금지. (3) **Look-ahead** — 모든 비-가격 변수는 "발표/공시/확정 시점"으로 시프트해야 한다.
- **우리 소스 매핑 현실**: 토스 = OHLCV 조회 전용(수급/구성 미제공), KIS = 체결+해외(수급은 최근 가집계만), 키움 = 보조(신용 화면, 과거 백필 약함). **비-가격 변수의 1차 무료 출처는 KRX 정보데이터시스템 + DART + SEC EDGAR + FRED** — 모두 신규 수집 모듈이 필요하다.
- **KR/US 비대칭이 최대 함정**: 투자자별 수급·공매도·신용·대차는 **KR 전용**(US 구조적 부재). 13F·FINRA SI·옵션·애널추정은 US 위주(KR은 유료). 모델에서 KR/US 분리 또는 결측 처리.

## 0-bis. ⚠ 실측 블로커: KRX 접근 차단 (2026-06 확인)

이 환경에서 **KRX(data.krx.co.kr)는 `GenerateOTP` 진입점부터 HTTP 200 / 본문 `LOGOUT`(6바이트) 반환** = 로그인 게이트. pykrx 1.2.8도 `KRX_ID/KRX_PW 미설정`으로 빈 응답. 즉 **KR 수급·공매도·KR PIT멤버십(전부 KRX 의존)은 현재 그대로는 불가**. 해소책:
1. **무료 KRX 데이터포털 계정**(data.krx.co.kr 회원가입) → `KRX_ID/KRX_PW` 환경변수 설정 → pykrx 로그인(가장 완전: 12분류 수급·깊은 이력·PIT).
2. **Naver Finance 폴백 스크레이핑**(로그인 불요, 이 환경에서 접근 성공 확인) — 종목별 외국인/기관 순매수·공매도 제공하나 12분류 없음·이력 얕음·취약.
3. KR 외부데이터 보류, **OHLCV 피처 + US 외부데이터(키리스)부터 진행**.

US 측(SEC EDGAR / FRED / GitHub 멤버십)은 **키 없이 즉시 가능**.

---

## 도메인 0 — 종목 메타 (최저비용·최우선, 처음 누락분)
OHLCV 외 가장 값싸면서 GKX 변수중요도 상위를 푸는 항목. 우리가 이미 붙이는 펀더멘털 수집기에 같이 딸려온다.
| 변수 | 정의 | 출처 | obtainable | 우선 |
|---|---|---|---|---|
| **상장주식수** | 시가총액(mvel1, GKX 통합 2위)·회전율(turn) 계산용 | US: SEC EDGAR companyfacts(CommonStockSharesOutstanding) / KR: DART | 키리스(US)/DART키(KR) | **high** |
| **섹터/산업 분류** | 산업모멘텀(indmom, GKX top-7)·sector-neutral rank | US: SEC SIC코드(무료)·GICS / KR: KRX 업종·DART | 키리스(US)/KRX·DART(KR) | **high** |
| 실제 지수 시계열 | KOSPI200/S&P500 등 지수레벨(beta·상대강도·레짐 정확화) | FRED(SP500,NASDAQCOM) / 토스 지수심볼 / 유니버스 합성 | 키리스/합성 | medium |
| 무위험금리(KR) | beta 초과수익·할인율 | 한국은행 ECOS(CD/기준금리) (US는 FRED) | 키리스 | low |

→ **상장주식수·섹터는 별도 수집이 아니라 펀더멘털(EDGAR/DART) 수집 시 같은 호출에서 확보.** 스키마: `stock_meta(symbol, date, shares_outstanding, sector_gics, sector_krx, source)` 추가.

---

## 도메인 1 — 한국 투자자별 수급/보유 (KR 전용)
| 변수 | 출처 | obtainable | 이력 | 우선 |
|---|---|---|---|---|
| 외국인 순매수(금액/수량) | KRX MDCSTAT023 / pykrx | KRX(로그인) | 2000s~ | **high** |
| 기관 12분류 순매수 | KRX MDCSTAT023(OTP) | KRX(로그인) | 깊음 | **high** |
| 개인 순매수 | KRX / pykrx | KRX(로그인) | 깊음 | medium(선형종속) |
| 외국인 보유율/한도소진율 | pykrx exhaustion_rates | KRX(로그인) | 깊음 | **high**(Δ권장) |
| 프로그램매매 차익/비차익 | KRX OTP(pykrx 미지원) | KRX(로그인) | 종목별 검증요 | medium |

**주의**: EOD 장마감 후(~18시) 확정 → t일 데이터는 t종가 이후 게이팅. 다중공선성(외국인↔보유율, 개인↔외국인+기관) → raw 대신 비율/변화율/잔차.

## 도메인 2 — 한국 공매도·신용·대차 (KR 전용)
| 변수 | 출처 | obtainable | 이력 | 우선 |
|---|---|---|---|---|
| 공매도 잔고비율 | pykrx shorting_balance | KRX(로그인) | **2016-06~** | **high** |
| 공매도 거래량/비중 | pykrx shorting_volume | KRX(로그인) | ~2017~ | **high** |
| 공매도 투자자별 | pykrx shorting_investor | KRX(로그인) | ~2017~ | medium |
| 신용융자 잔고(종목별) | 키움/KIS 화면 / FnGuide(유료) | API(go-forward) | 과거 약함 | **high**(go-forward) |
| 대차잔고(종목별) | SEIBro | 스크레이프(상) | 수년 | medium(공매도와 중복) |

**주의**: T+1~T+2 지연 발표 → "발표시점" 시프트 필수. **공매도 금지구간(2020-03~2021-05, 2023-11~2025-03)은 0/결측 → 레짐 더미**.

## 도메인 3 — 지수/ETF 편입편출 PIT (KR+US, 생존편향)
| 변수 | 출처 | obtainable | 이력 | 우선 |
|---|---|---|---|---|
| KOSPI200/KOSDAQ150 PIT | pykrx index_portfolio_deposit_file | KRX(로그인) | ~2014~ | **high** |
| S&P500 PIT | GitHub fja05680/sp500 (MIT) | 키리스 | 1996~ | **high** |
| NASDAQ100 PIT | GitHub jmccarrell/n100tickers | 키리스 | 2015~ | **high** |
| 탈락/상폐 종목 가격(완전 생존편향) | stooq/yfinance(불완전) | hard | 부분 | medium(별도 PR) |

**결정적 한계**: PIT 멤버십 마스크 ≠ 완전 생존편향 제거(현 856종목 한정). 1차는 "PIT 소속 플래그 + add/drop 더미"로 이벤트 효과 검증, 완전 보정은 탈락종목 가격 적재 별도 과제. **US는 즉시(GitHub), KR은 KRX 로그인 후.**

## 도메인 4 — 펀더멘털/밸류에이션 (US 키리스 / KR DART 키 필요)
| 변수 | 출처 | obtainable | 이력 | 우선 |
|---|---|---|---|---|
| PBR, ROA | KR DART / US EDGAR + 주가 | DART키 / EDGAR키리스 | KR2015~/US2009~ | **high**(난이도 최저) |
| PER/PSR/ROE(TTM), 매출·이익 성장 | DART/EDGAR | DART키 / 키리스 | 동일 | **high** |
| PCR, EV/EBITDA, 배당 | DART/EDGAR | DART키 / 키리스 | 동일 | medium~high |
| 어닝 서프라이즈/SUE | KR FnGuide(유료) / US 부분무료 | paid(KR) | — | defer |

**look-ahead 핵심**: "기간말"이 아닌 "공시시점"(KR rcept_dt / US EDGAR acceptance-datetime)+1거래일부터 사용. 10-Q YTD 누적 → 분기 차감 후 TTM. 정정공시 vintage 보존 권장.

## 도메인 5 — 미국 기관/공매도/옵션/추정치 (US)
| 변수 | 출처 | obtainable | 이력 | 우선 |
|---|---|---|---|---|
| 13F 기관보유 변화 | SEC DERA 13F | 키리스(CUSIP매핑 상) | 2013Q2~ | high(분기지연) |
| 애널 추정·리비전/SUE | Finnhub/FMP(부분)→IBES(유료) | paid(깊은이력) | — | high(비용큼) |
| Short interest(격주) | FINRA SI API(OAuth2) | 키 | **2021-06~** | medium(전구간 미달) |
| 옵션 OI/IV/PCR(종목별) | ORATS/OptionMetrics | paid | — | defer(시장PCR/VIX만 무료) |

## 도메인 6 — 감성·대체·매크로 (KR+US)
| 변수 | 출처 | obtainable | 이력 | 우선 |
|---|---|---|---|---|
| **미국채10Y·곡선·VIX·USD/KRW·DXY·유가** | FRED | 키리스 CSV 가능 | 1962/1990~ | **high(최고 가성비)** |
| DART 공시 이벤트·실적 YoY 가속(KR) | DART OpenAPI | DART키 | 1999~ | **high**(KR 최고 ROI) |
| SEC EDGAR 공시 이벤트(US) | EDGAR EFTS | 키리스 | 2001~ | medium |
| 미국 뉴스 감성(FinBERT) | Alpha Vantage/Finnhub | 키 | **2022-03~** | high(라이브)/백테스트 제약 |
| KR 뉴스 감성(KR-FinBERT) | 네이버/BigKinds | 스크레이프(상) | go-forward | medium |
| Google Trends/Reddit/X | pytrends/유료 | 빈약/유료 | — | defer |

---

## 종합 권고 순서
1. **즉시(키리스·전구간·PIT 안전)**: FRED 매크로 → US PIT 멤버십(GitHub) → US 펀더멘털(SEC EDGAR, PBR·ROA 먼저) → **OHLCV 파생 피처**(별도, 데이터 불요).
2. **자격증명 후**: KRX 로그인 → KR 수급+공매도+KR PIT멤버십 / DART 키 → KR 펀더멘털·공시.
3. **go-forward 적재**: 신용잔고(종목별), 뉴스 감성.
4. **유료/보류(defer)**: 어닝 서프라이즈(KR), 옵션 종목별, 애널 리비전 깊은이력, 13F(매핑비용), 소셜, FINRA SI(전구간 미달), 탈락종목 가격(별도 PR).

**모든 채택은 purged+embargo CV의 IC/ICIR + 다중검정 보정 통과 시에만 모델 투입.**

## 제안 ClickHouse 스키마
- `stock_investor_flow` (date, symbol, market, foreign/inst/retail/pension/trust/... net_value·net_qty, source, ingested_at) — KR 종목별 일별 투자자 순매수. PARTITION toYYYYMM(date), ORDER BY (symbol,date)
- `stock_foreign_holding` (date, symbol, shares_held, foreign_limit_qty, exhaustion_rate, holding_ratio, ...) — 외국인 보유율. 피처는 Δratio
- `stock_short` (date, symbol, short_volume/value, short_balance_qty/value/ratio, short_volume_ratio, ban_regime, ...) — KR 공매도. T+2 지연 → publish_date 별도/시프트
- `stock_margin_loan` (date, symbol, margin_loan_qty/value/ratio, source) — 신용융자(go-forward)
- `index_membership` (date, symbol, index_name, is_member, weight, source) — PIT 마스크
- `index_rebalance_event` (announce_date, effective_date, symbol, index_name, action(add/drop), source) — 편입편출 이벤트(발표일 vs 적용일 분리)
- `fundamentals_quarterly` (symbol, period_end, filed_date, fiscal_period, revenue/operating_income/net_income/total_equity/total_assets/operating_cashflow/shares_outstanding, is_cumulative, source(DART/EDGAR), vintage_ingested_at) — 원시 재무(PIT: filed_date 게이팅)
- `fundamentals_ratios_daily` (date, symbol, per/pbr/psr/pcr/ev_ebitda/roe/roa/rev_growth_yoy/earn_growth_yoy/dividend_yield, as_of_filed_date) — 일별 배수(filed_date×종가)
- `news_sentiment_daily` (date, symbol, sentiment_mean/std, news_count, source, model) — published_at 기준 집계
- `macro_daily` (date, dgs10/dgs2/dgs3mo/t10y2y/t10y3m/vix/usdkrw/dxy/wti/brent, source) — 전종목 공통 매크로. 휴일 전일캐리
