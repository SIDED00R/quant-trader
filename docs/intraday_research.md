# 인트라데이(분봉) 주식 매매 방법 전수조사

> 분 단위(1~60분, 틱 아님) 주식 자동매매 방법을 학술·실무 문헌으로 조사하고, **비용 차감 후 실용성** 관점에서 후보를 가린다. 연구→검증→조건부배포 파이프라인의 1단계 산출물. 검증·배포는 단계 2~4에서.
> 톤 규약(`docs/kiwoom.md` 준수): 검증된 사실만 단정, 불확실은 **확인필요**, 출처는 저자·연도·매체로 인라인.

## 0. 검증 맥락 (이 조사의 전제)
- **유니버스:** KOSPI 200 + KOSDAQ 150 + S&P 500 + NASDAQ(~950종목), **long-or-cash(공매도 없음)**.
- **거래비용:** 국내 왕복 ≈ **0.30%**(수수료 0.10% + 매도 거래세 0.20%, 2026 인상분), 미국 매도세 ~0(SEC/TAF 미미). → **한·미 비용을 분리 평가**해야 한다.
- **데이터:** 분봉 OHLCV(히스토리 깊이 제한 가능). 호가/틱이 필요한 방법인지 명시.
- **검증 규율:** walk-forward + **Deflated Sharpe**(다중검정 보정). 채택 기준 = "비용 차감 후 OOS 양수 AND DSR ≥ 0.90"(`docs/baseline.md`와 동일).

## 1. 횡단 증거 (모든 결론의 배경)
1. **비용이 1차 필터.** 이 팀은 코인 1분봉 SMA에서 수수료가 자본의 83%를 잠식해 −97.97%를 직접 겪었다(`docs/baseline.md`). 단순 인트라데이 신호 다수는 비용 차감 후 사망한다.
2. **bid-ask bounce 함정.** 1시간 미만 반전 수익의 상당 부분이 호가 스프레드 안에 있어, **분봉 OHLCV(종가)만으로 백테스트하면 과대평가**된다(Heston-Korajczyk-Sadka 2010, JF; Conrad-Gultekin-Kaul 1997, RFS; Novy-Marx NBER w30917). 반전 계열은 호가/지정가 체결 가정 없이는 신뢰 불가.
3. **데이터 스누핑.** 단순 기술규칙 초과수익은 다중검정 보정 시 크게 약화(Sullivan-Timmermann-White 1999, JF). → Deflated Sharpe 필수(Bailey-López de Prado: 5년 데이터·독립시도 ~45회면 IS Sharpe 1 / OOS 0이 거의 보장).
4. **생존편향이 모멘텀/횡단면에 치명적.** 현재 구성종목으로 과거를 백테스트하면 alpha를 대형 1~2%p, 신흥/소형 4.9%p까지 과대평가(모멘텀×생존편향은 alpha ~40% 소실 사례). → **point-in-time 구성 + 상장폐지 포함** 필수.
5. **한국 특수성.** 매도세 0.20%(왕복비용 최대 비중, 회전율 직격) + 공매도 금지 이력(2023.11~2025.03 전종목) + **VI(변동성완화장치, 2분 단일가→시장가 불가)·±30% 상하한가 락(청산 불가)** 회피 로직 필요. → KR은 저회전·대형주·long-only, 고회전은 US(매도세 0)로.

## 2. 방법군별 분석

각 방법: 원리 / 핵심 근거 / 분봉 적합 / **비용 민감도(왕복 0.30% 생존)** / 데이터 요구 / 과적합·빈도 / long-or-cash·대규모 유니버스 적합 / **판정**.

### 2.1 모멘텀·추세 (단일종목 시계열)
- **인트라데이 시계열 모멘텀** — 초반 구간 수익이 후반을 예측. Gao-Han-Li-Zhou(2018, JFE: 첫 30분→마지막 30분, **단 시장 ETF 레벨 — 개별종목 적용강도는 확인필요**). Zarattini-Aziz-Barbon(2024, SSRN 4824172: SPY 비정상 수급 진입+트레일링스톱, 비용차감 후에도 우수). Schulmeister(2009, RFE: 일봉 기술모델은 소멸했으나 **30분봉은 여전히 수익**). 적합 15~30분 · 비용민감 **중간** · 호가 불필요 · 빈도 낮음~중간 · 유니버스 적합 높음. **판정: 채택 후보(저회전·강신호 한정).**
- **MA 교차/MACD** — 휩쏘로 빈번 진입 → 비용 차감 후 **사망 유력**, 과적합 높음, MACD 고유 근거 빈약(확인필요). **판정: 보조(추세 확인)만.**

### 2.2 평균회귀 (가장 위험)
- **단기/인트라데이 reversal, 볼린저, RSI** — 패자 매수(유동성 공급 보상). Nagel(2012: 평상시·소형주는 스프레드 차감 후 소멸). 수익이 **bid-ask bounce 내부**(§1.2). 비용민감 **매우 낮음(최취약)** · 호가/틱 사실상 필요 · 빈도 높음. long-or-cash에선 매수 측 절반만. **판정: 단독 제외**(시장가·왕복 0.30%·OHLCV에서 거의 확실히 음수). 단 **횡단면 종가직전 리버설**(§2.4)은 예외.

### 2.3 돌파 (필터링 시 유망)
- **Opening Range Breakout(ORB)** — 순진한 ORB는 죽음(7,000종목 2016-23, 연 IRR 3.2%/Sharpe 0.48 < S&P500). **그러나 필터링하면 생존:** Zarattini-Barbon-Aziz(2024, SSRN 4729284 "Stocks in Play": 첫 5분 상대거래량≥100% 상위 20 → Sharpe 2.81/연알파 36%, 비용차감 후 유지; **단 레버리지 전제·선택편향 우려**). 적합 5분 OR · 비용민감 **중간**(하루 1회 저회전) · 호가 불필요 · 빈도 낮음 · 유니버스 적합 **가장 높음**. **판정: 채택 후보(거래량 필터 필수).**
- **Donchian/변동성 돌파(Crabel)** — ORB 동류, 인트라데이 주식 동료심사 근거 약함(확인필요), 횡보 휩쏘. **판정: 돌파 3종 묶어 함께 검증.**

### 2.4 횡단면 랭킹 (대규모 유니버스의 본진 — 가장 유망)
- **종가직전 리버설(End-of-Day Reversal)** — Baltussen-Da-Soebhag(2024/25, SSRN 5039009: 장중 패자가 마지막 30분에 long-short 일평균 **0.24%** 아웃퍼폼, 원인=리테일 contrarian 매수). **분봉 OHLCV로 구현되는 유일한 마이크로구조성 효과(틱 불필요).** 비용민감 **중간**(US 일부 생존; **KR은 0.24% gross < 0.30% 비용 → 단독 잠식 위험, 실증 필요**). long-only 적합 **우수**(리테일이 숏 못 해 패자 매수 → 롱 다리가 본질). **판정: 채택 후보, 미국 1순위.**
- **단기 리버설(횡단면, 대형주 한정)** — Conrad-Gultekin-Kaul(1997: contrarian 이익 대부분 bid-ask bounce, 소형주 소멸). Quantpedia(1990-2009: **100대 대형주·주간 리밸런스, 비용차감 연 16.25%/Sharpe 1.09** — 소형주 과다거래가 비용 충격 원인 → 대형 한정 시 주 30-50bp 순생존). 비용민감 낮음(소형)~중간(대형). **판정: 채택 후보(대형주 한정).**
- **횡단면 모멘텀/상대강도** — Blitz-Baltussen-van Vliet(FAJ 2020 "When Equity Factors Drop Their Shorts": 부가가치 대부분이 **롱 다리** — 롱 alpha +1.09% 유의, 결합 롱 Sharpe 1.10 vs 숏 0.69). HKS(2010: 30분 인트라데이 횡단면 모멘텀 40거래일 지속, 단 순알파보다 **실행 타이밍 최적화 용도**). 적합 30분~일 · long-only 적합 **우수**(공매도 못 해도 알파 대부분 보존). **판정: 채택 후보.**

### 2.5 통계적 차익 (long-or-cash에서 반신불수)
- **Pairs/공적분** — GGR(2006, RFS: 자기금융 페어 연 ~11%, **2000년대 이후 단순 버전은 비용차감 후 알파 소멸**). Avellaneda-Lee(2010, QF: PCA Sharpe 1.44→0.9, ETF 1.1→1.51). 시장중립의 본질=양다리인데 **공매도 불가 시 한쪽만 남아 알파 절반+ 소실 → 횡단면 리버설로 환원**(Taiwan 실증 Chen et al. 2018, IREF). 한국 공매도 금지 이력 = 제도 리스크. **판정: 독립 전략 제외. 단 PCA 잔차를 "롱 후보 랭킹 신호 생성기"로 재활용은 의미 있음.**

### 2.6 마이크로구조 / order-flow (분봉 OHLCV로 부적합)
- **OBI/OFI** — Cont-Kukanov-Stoikov(2014: OFI가 단기 가격변화 near-linear 예측 R²≈65%, **강함은 수십 초**; imbalance 최대 20분 유의하나 분당 급감). **호가(L1 최소 L2 권장) 필수 — 분봉 OHLCV로 계산 불가.**
- **VPIN** — Andersen-Bondarenko(2014: 단기 변동성 빈약한 예측자, 예측력은 거래강도와의 기계적 관계, 체결부호 필요). **판정: 방향 알파로 제외**(신호수명·데이터·비용·950종목 실시간 호가 처리 모두 retail 비현실). 가격압력 되돌림은 §2.4 종가직전 리버설로 분봉만으로 취함.

### 2.7 트리 기반 ML (가장 현실적 ML)
- **Gradient Boosting(LightGBM/XGBoost)** on 특징(수익률·변동성·기술지표·거래량) — Gu-Kelly-Xiu(2020, RFS: 비선형 OOS R² 0.33~0.40% > 선형, **단 월봉·횡단면**). López de Prado *Advances in Financial ML*(triple-barrier 라벨링·meta-labeling·fractional diff·uniqueness weighting·**purged/embargo CV·CPCV**). 비판: 다수 GBM 논문이 비용·누설·다중검정 미통제로 과장된 정확도 보고. **CPU 학습·ms 추론·랭킹형** → ML 후보 중 1순위. **판정: 채택 후보(규율·비용게이트 전제).**

### 2.8 딥러닝 시퀀스 (연구 트랙만)
- **LSTM/GRU·Transformer·TCN** — Lim-Zohren-Roberts(2019: 딥모멘텀, Sharpe 직접최적화+turnover 페널티, **선물·비용 2~3bp까지** 우위). DeepLOB(호가 데이터 필요 — 본 과제 OHLCV와 층위 다름). 비판: Zeng et al.(2023, AAAI: 단순 DLinear가 다수 Transformer 능가 — 저 신호대잡음 경고); "5분 주식수익률 예측 불가" 실증. **소표본·고노이즈 = 과적합 최적조건. GPU 필요.** **판정: 연구 트랙만, 트리 대비 비용차감 우위 입증 전 배포 금지.**

### 2.9 강화학습 (최후순위)
- **DQN/PPO 등** — FinRL(2020). 비판(가장 강함): 비정상성·표준부재로 재현 불가, 하이퍼파라미터 재튜닝이 백테스트 과적합 유발, sim-to-real 갭·보상해킹(체계적 리뷰 arXiv 2512.10913). **판정: 탐색용 최후순위(재현성·체결모델 선결).**

### 2.10 변동성 (가장 견고 — 예측이 아닌 리스크 정규화)
- **변동성 타게팅/관리** — 변동성 스케일링이 모멘텀 Sharpe 개선(Moskowitz; Daniel-Moskowitz). Moreira-Muir(2017) vs **반박** Cederburg et al.(2020, JFE: 효과는 모멘텀류 국한·OOS 비유의). Corsi(2009: HAR-RV가 GARCH류보다 정확). 예측이 아니라 익스포저 정규화라 **비용·과적합 취약성 낮음, CPU, 팀이 일봉에서 이미 채택**(`trend.py` vol-targeting). **판정: 채택 후보 1순위(사이징 레이어로 다른 신호와 결합).**

## 3. shortlist (검증 대상) & 배치

| 우선 | 방법 | 빈도 | 시장 배치 | 근거 요약 |
|---|---|---|---|---|
| 1 | **변동성 타게팅/관리**(분봉 RV/HAR-RV 익스포저) | 추정=분봉·리밸런싱=저빈도 | KR+US | 근거 최견고, 비용·과적합 취약성 최저, 이미 일봉 채택 |
| 2 | **횡단면 종가직전 리버설**(롱 다리) | 일 1회(마감 전) | **US 우선**, KR 실증 | 분봉 OHLCV로 구현, long-only 본질, US 매도세 0 |
| 3 | **횡단면 모멘텀/상대강도**(롱 다리) | 30분~일 | KR+US | Blitz et al. 롱 다리 알파 보존, 대규모 유니버스 본진 |
| 4 | **필터링 돌파(ORB "stocks in play")** | 5분 OR·하루 1회 | KR+US | 거래량 필터 시 비용차감 생존, 저회전 |
| 5 | **트리 ML(LightGBM)** + triple-barrier/meta-labeling | 가변(저회전 라벨) | KR+US | 현실적 ML, CPU, 규율·비용게이트 전제 |
| 보조 | 저회전 인트라데이 TS 모멘텀 / PCA 잔차→랭킹 신호 / MA·Donchian(추세확인) | — | — | 단독 약함, 결합·필터로만 |
| 제외 | 단기반전 단독 · pairs 독립전략 · OBI/OFI/VPIN · DL/RL 배포 | — | — | bid-ask bounce / long-only破 / 호가·저지연 / 비용·과적합 |

**선정 4대 생존 조건:** ① 회전율 억제(비용) ② 한국 매도세 0.20% → 고회전은 US로 ③ 대형주·long-only 횡단면 ④ point-in-time 검증.
**빈도 탐색:** 1분 한 벌 확보 시 `--bar-min`으로 5/15/30/60분 무료 측정 → 비용 게이트를 통과하는 빈도를 데이터로 결정.
**GPU:** shortlist 1~5는 전부 CPU(트리·규칙). **DL/RL이 실제 학습에 들어갈 때만** GPU VM(플랜 단계 4.0).

## 4. 검증 규율 요구사항 (단계 3로 인계)
- **분봉 연율화 보정(선결):** `metrics.SECONDS_PER_YEAR`(24/7 가정)을 정규장 분봉(거래일 252×390분/년)으로 — 미보정 시 Sharpe·DSR 거짓 양성.
- **purged + embargo walk-forward CV**(López de Prado): ML 특징/라벨 창이 IS/OOS 경계를 넘는 누설 차단.
- **다중검정 N 정직 반영:** DSR `n_trials` = 시도한 빈도×방법×파라미터 총수.
- **point-in-time 구성 + 상폐 포함:** 생존편향(§1.4).
- **비용 게이트:** KR 0.30%/US 비용 차감 + 슬리피지 0/5/10/20bps 스윕 + 거래빈도·비용/자본 상한.
- **bid-ask bounce 회피:** 반전 계열은 종가/지정가 체결 가정(시장가 OHLCV 백테스트 과대평가 경계).
- **KR 체결 제약:** VI 단일가·±30% 상하한가 락 시 시장가 청산 불가 → 회피/대기 로직.

## 5. 정직한 예상
왕복 0.30%(KR) + 팀 자체 증거를 보면 **단일종목 순수 1분 통과 확률은 낮다.** ~950종목 대규모 유니버스에서는 **횡단면(롱 다리 리버설/모멘텀)** 이 breadth로 통계 power를 얻어 중빈도(15/30/60분·일1회)에서 통과할 여지가 가장 크고, **변동성 타게팅**이 어떤 신호든 위험조정성과를 보강한다. 차선 = 필터링 돌파·트리 ML(규율 전제). 전부 비용 게이트 미달이면 **일봉 앙상블 유지가 검증된 결론**(정당한 음성 결과). DL/RL은 가능성을 넓히나 과적합·다중검정 통제가 부실하면 가장 위험한 거짓양성원이다.

## 6. 출처 (저자·연도·매체) & 확인필요
**핵심 출처:** Schulmeister(2009, RFE) · Sullivan-Timmermann-White(1999, JF) · Heston-Korajczyk-Sadka(2010, JF) · Gao-Han-Li-Zhou(2018, JFE) · Zarattini-Aziz-Barbon(2024, SSRN 4824172) · Zarattini-Barbon-Aziz(2024, SSRN 4729284) · Nagel(2012) · Conrad-Gultekin-Kaul(1997, RFS) · Baltussen-Da-Soebhag(2024/25, SSRN 5039009) · Blitz-Baltussen-van Vliet(FAJ 2020) · GGR(2006, RFS) · Avellaneda-Lee(2010, QF) · Chen et al.(2018, IREF) · Cont-Kukanov-Stoikov(2014) · Andersen-Bondarenko(2014) · Gu-Kelly-Xiu(2020, RFS) · López de Prado(*Advances in Financial ML*, 2018) · Lim-Zohren-Roberts(2019) · Zeng et al.(2023, AAAI) · Moreira-Muir(2017) · Cederburg et al.(2020, JFE) · Corsi(2009, HAR-RV) · Bailey-López de Prado(Deflated Sharpe).

**확인필요(추가 검증 권장):** Gao et al.의 개별종목(비ETF) 적용강도 · End-of-Day Reversal의 *비용 차감 순수익*(특히 KR) · Donchian/Crabel 인트라데이 주식 동료심사 증거 · MACD 고유 학술근거 · Schulmeister 2000년 이후·한국 재현 · 한국 KDA order-imbalance 분당 계수 · RL/DL의 주식 분봉·30bp 비용 생존 사례.
