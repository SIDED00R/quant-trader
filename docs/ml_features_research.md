# 주식수익 예측 피처 카탈로그 — 일봉 OHLCV 환경 (856종목, 7.1년)

> DL/ML 주식예측 문헌 전수조사(9개 연구 렌즈, 213개 피처)에서 추출한 피처/파생변수를 **우리 데이터로 계산 가능한지** 태깅해 정리한다. 자매 문서: 데이터 확보 계획 [ml_data_acquisition.md](ml_data_acquisition.md).
>
> **환경 전제**: ClickHouse에 KR 344 + US 512 = 856종목 일봉 OHLCV 7.1년치. 펀더멘털·호가(LOB)·뉴스 **없음**. 검증은 **purged/embargo CV + IC(정보계수)**.
>
> **정직성 원칙 (위반 금지)**:
> - 아래 어떤 피처도 "유용하다"고 **가정하지 않는다**. 모든 피처는 Rank IC / ICIR / purged-embargo CV로 **측정 대상**이다.
> - **생존편향**: 우리 유니버스 = 현재 구성종목 = look-ahead bias. 과거 시점에 지수에 없었거나 상폐된 종목이 빠져 횡단면 백테스트가 낙관 편향.
> - **거래비용 게이트**: 단기 반전(mom1m·주간반전·MaxRet)은 IC가 높아도 회전율 폭증으로 순알파 소멸 가능. IC와 별개로 비용게이트 통과 필수.
> - **태그**: `computable_now=✅`는 현재 일봉 OHLCV(+856 단면)만으로 계산 가능. 호가가 필요한 baspread는 Corwin-Schultz/Abdi-Ranaldo 고저가 추정으로 근사하므로 ✅.

---

## 0. 핵심 결론 (문헌의 직접 함의)

Gu-Kelly-Xiu(2020, RFS) 변수중요도(Fig.5)에서 **모든 ML 모델(트리·NN·선형) 공통 최상위 예측변수 대부분이 일봉 OHLCV만으로 계산 가능**:
- **가격추세**: mom1m(단기반전), mom12m, mom6m, mom36m(장기반전), chmom, maxret
- **유동성**: dolvol, ill(Amihud), zerotrade, std_dolvol, baspread(고저가 추정)
- **위험/변동성**: retvol, idiovol, beta, betasq

펀더멘털 비율(ep/sp/bm/agr)은 **더 낮은 중요도**. → **OHLCV만으로 GKX 예측력의 상당 부분 복제 가능**이 문헌의 직접 함의. 단 Green-Hand-Zhang(2017)은 94개 중 2003년 이후 **2개만 독립 유의** 경고 → 예측력 감쇠 크므로 **반드시 우리 IC로 재측정**.

계산 불가(데이터 추가): mvel1/turn/std_turn(주식수), indmom/sector-neutral(섹터 매핑), valuation·accounting(재무제표), VPIN/OFI/true Kyle λ(호가·틱).

---

## 1. 가격추세: 모멘텀 / 반전 (대부분 즉시 계산 가능)

| 피처 | 정의 / 수식 | data_required | now | 출처 |
|---|---|---|---|---|
| **mom12m** (12-1) | P(t-21)/P(t-252)-1, 최근 1개월 skip | ohlcv | ✅ | Jegadeesh-Titman(1993); GKX |
| **mom6m** | P(t-21)/P(t-126)-1 | ohlcv | ✅ | Jegadeesh-Titman(1993) |
| **mom1m** (단기반전 STR) | -1×(P(t)/P(t-21)-1) | ohlcv | ✅ | Jegadeesh(1990); Lehmann(1990) |
| **reversal_1w** | -1×(P(t)/P(t-5)-1) | ohlcv | ✅ | Lehmann(1990) |
| **mom36m** (장기반전 LTR) | P(t-273)/P(t-756)-1, 부호반전 | ohlcv | ✅ | De Bondt-Thaler(1985) |
| **chmom** (모멘텀 가속) | (직전 6m수익)-(그 이전 6m수익) | ohlcv | ✅ | Gettleman-Marks(2006); GKX |
| **High52** | P(t)/max(High,252d) | ohlcv | ✅ | George-Hwang(2004) |
| **PricePosition_252** | (P-min(L,252))/(max(H,252)-min(L,252)) | ohlcv | ✅ | Stochastic %K 동형 |
| **TSMom** (시계열 모멘텀) | sign(r(t-252,t-21)) 또는 /σ | ohlcv | ✅ | Moskowitz-Ooi-Pedersen(2012) |
| **MomVolScaled** (크래시 완화) | mom12m / σ_realized(126d) | ohlcv | ✅ | Barroso-Santa-Clara(2015) |
| **FIP_ID** (Frog-in-the-Pan) | sign(PRET)×(%neg-%pos) | ohlcv | ✅ | Da-Gurun-Warachka(2014) |
| **overnight/intraday 분해** | o=ln(O_t/C_{t-1}), c=ln(C_t/O_t) 누적 | ohlcv | ✅ | Lou-Polk-Skouras(2019) |
| **MomSeason** | 과거 2~5년 같은 월 평균수익 | ohlcv | ✅ | Heston-Sadka(2008) |
| **indmom** (산업 모멘텀) | 산업 시총가중 12m 모멘텀 | cross_sec | ❌ 섹터 | Moskowitz-Grinblatt(1999) |
| **ResidMom_CAPM** | 시장 회귀잔차 12-1 평균/σ | cross_sec | ✅ (근사) | Blitz-Huij-Martens(2011) |
| **DynMom_panic** | 약세+고변동성 게이트 × 모멘텀 | cross_sec | ✅ | Daniel-Moskowitz(2016) |

**주의**: 모멘텀은 반드시 **최근 1개월 skip**(단기반전 오염 제거). 7.1년(~1785거래일)이라 mom36m는 유효표본 ~4년 → ICIR 불안정 가능.

---

## 2. 변동성 / 레인지 추정량 (전부 즉시 계산 가능)

일봉 close-only 변동성은 노이즈 큼. **High-Low 레인지 추정량은 같은 표본에서 5~14배 효율** → 일봉 환경에서 특히 가치. σ² 계산 후 √(×252 연율화).

| 피처 | 수식(단일일) | 효율 | now | 출처 |
|---|---|---|---|---|
| RV_cc | σ²=(1/(N-1))Σ(r-r̄)², r=ln(C/C_{-1}) | 1.0 | ✅ | RiskMetrics |
| Parkinson | 0.361(ln H/L)² | ~5× | ✅ | Parkinson(1980) |
| Garman-Klass | 0.5(ln H/L)²-0.386(ln C/O)² | ~7.4× | ✅ | Garman-Klass(1980) |
| Rogers-Satchell | ln(H/C)ln(H/O)+ln(L/C)ln(L/O) | ~8× | ✅ | Rogers-Satchell(1991) |
| GKYZ | GK+(ln O_t/C_{t-1})² | >GK | ✅ | Yang-Zhang 변형 |
| **Yang-Zhang** | σ_o²+kσ_c²+(1-k)σ_RS² | ~14× (최고) | ✅ | Yang-Zhang(2000) |
| EWMA | σ²_t=λσ²_{t-1}+(1-λ)r²_{t-1}, λ=.94 | — | ✅ | RiskMetrics |
| GARCH(1,1) | ω+αr²+βσ²; α+β도 피처 | — | ✅ expanding refit | Bollerslev(1986) |
| Vol-of-vol | ln σ 롤링 std | — | ✅ | Baltussen(2018) |
| ATR | TR=max(H-L,\|H-C_{-1}\|,\|L-C_{-1}\|); EMA_14 | — | ✅ | Wilder(1978) |
| Vol term-structure | σ_short/σ_long; HAR-RV(일/주/월) | — | ✅ | Corsi(2009) |
| retvol | 직전 N일 일수익 std, N∈{21,63,126,252} | ohlcv | ✅ | Ang et al.(2006) |
| realized semivariance | RS±=Σr²·1[r≷0]; signed jump | ohlcv | ✅ | Bollerslev(2020) |
| skew/kurt | 직전 N일 일수익 왜도·첨도 | ohlcv | ✅ | Amaya et al.(2015) |
| idiovol/IVOL | 시장 회귀잔차 std | cross_sec | ✅ (근사) | Ang et al.(2006) |

**누설 주의**: GARCH/HMM/표준화는 **시점 t까지로만 적합**(expanding/rolling refit). 856종목 개별 GARCH는 비용 큼 → 패널/풀링 또는 주기적 재적합.

---

## 3. 유동성 / 거래량 미시구조

| 피처 | 수식 | data_required | now | 출처 |
|---|---|---|---|---|
| **ill (Amihud)** | (1/D)Σ\|R\|/(C·V), log/rank | volume | ✅ | Amihud(2002) |
| ΔILLIQ | ILLIQ_5d - ILLIQ_63d (유동성 충격) | volume | ✅ | Acharya-Pedersen(2005) |
| **dolvol** | ln(월평균 C·V), 21d ADV, rank | volume | ✅ | GKX; Brennan(1998) |
| std_dolvol | 일별 로그거래대금 std | volume | ✅ | Chordia(2001) |
| volume z-score/RVOL | (V-MA_n)/SD_n; V/MA_n | volume | ✅ | Gervais(2001) |
| zerotrade | 직전 1개월 거래량=0 일수 | volume | ✅ | Liu(2006) |
| Roll spread | 2√(-Cov(ΔP,ΔP_{-1})) | ohlcv | ✅ | Roll(1984) |
| **Corwin-Schultz** | 고저가비율 유효스프레드, 음수절단 | ohlcv | ✅ | Corwin-Schultz(2012) |
| **Abdi-Ranaldo** | 2√max(E[(c-η)(c-η_{+1})],0) | ohlcv | ✅ | Abdi-Ranaldo(2017) |
| Kyle λ proxy | ILLIQ; \|R\|=λ√(C·V) 회귀 | ohlcv | ✅ (근사) | Kyle(1985) |
| signed dollar volume | sign(R)·(C·V) n일 합 | ohlcv | ✅ | Lee-Swaminathan(2000) |
| BVC 주문불균형(일봉) | 2Φ((C-O)/σ_ΔP)-1 | ohlcv | ✅ (거친근사) | Easley-LdP-O'Hara(2016) |
| turn/std_turn | V/상장주식수 평균·std | fundamentals | ❌ 주식수 | Datar(1998) |
| VPIN, OFI | 볼륨버킷 / 호가 큐 변화 | quotes_lob | ❌ | Easley(2012); Cont(2014) |

**주의**: ILLIQ ≈ Kyle λ proxy(중복 회피). ILLIQ는 volume과 강상관(다중공선성 점검). CS·AR 둘 다 넣고 IC 비교로 선택.

---

## 4. 공식형 알파 라이브러리 (Qlib / WorldQuant / GTJA)

### 4-A. Qlib Alpha158 — **전부 즉시 계산 가능** (loader.py 검증), 윈도 d∈{5,10,20,30,60}
| 그룹 | 피처 | 수식 예 |
|---|---|---|
| KBar(9) | KMID,KLEN,KMID2,KUP,KUP2,KLOW,KLOW2,KSFT,KSFT2 | KMID=(C-O)/O; KSFT=(2C-H-L)/O |
| 가격정규화 | O/H/L/(VWAP)/C @ lag0-4 / C | Ref($f,d)/C |
| 추세/모멘텀 | ROC,MA,STD,BETA,RSQR,RESI | ROC=Ref(C,d)/C; BETA=Slope(C,d)/C |
| 위치/극값 | MAX,MIN,QTLU,QTLD,RANK,RSV,IMAX,IMIN,IMXD | RSV=(C-Min(L,d))/(Max(H,d)-Min(L,d)) |
| 가격-거래량 | CORR,CORD | Corr(C,log(V+1),d) |
| 방향/강도 | CNTP,CNTN,CNTD,SUMP,SUMN,SUMD | SUMP=Σmax(ΔC,0)/Σ\|ΔC\| (RSI류) |
| 거래량 | VMA,VSTD,WVMA,VSUMP,VSUMN,VSUMD | WVMA=Std(\|ΔC/C\|·V)/Mean |

VWAP 항은 일중 VWAP 없음 → **(H+L+C)/3 근사**.

### 4-B. Qlib Alpha360 — DL 입력, 즉시 계산
과거 60거래일 6채널(O,H,L,C,VWAP,V)을 당일 C/V로 정규화한 raw 시퀀스(6×60). GRU/LSTM/Transformer/TCN 표준 입력. VWAP 채널 근사 또는 **5채널(300)로 축소 권장**. → **DL 경로 1순위 입력**.

### 4-C. WorldQuant 101 / GTJA191
- 평균 보유 0.6~6.4일, 평균 쌍상관 15.9%(저상관→앙상블 적합). **단일 IC 작음 → 앙상블 전제**.
- `rank`/`ts_rank`/`correlation`/`decay_linear` 연산자. rank류는 856 단면(KR/US 분리).
- **vwap 핵심 알파(#5,#41,#42)는 (H+L+C)/3 근사 시에만**. Alpha#101=(C-O)/(H-L)=KMID2(라이브러리 간 중복).
- GTJA191은 중국시장치 → **KR/US 일반화는 IC로 재검증**, vwap/amount 알파 제외.

---

## 5. 기술적지표 (정직한 경고)

> **경고**: arXiv:2412.15448(RF+12지표) train R²0.75~0.81인데 **test R² 음수, 방향정확도 86%→48% 붕괴**. RSI/MACD 단독 OOS 예측력 약함. GKX에서도 oscillator는 상위 예측자 못 듦. **레벨이 아니라 (a)z-score/분위정규화, (b)변화/기울기, (c)레짐 조건부 상호작용으로 피처화** 후 IC 측정.

RSI(14)→(RSI-50)/50·delta·돌파더미 · MACD→Histogram 부호전환·기울기 · Bollinger %b/Bandwidth · Stochastic %K/%D(=RSV) · ADX/DI(추세강도 게이트) · CCI/CMO/Williams%R · MA ratio/교차(연속거리·기울기) · Trend factor(Han-Zhou-Zhu MA_d/P 다중) · OBV/WROBV(차분) · 캔들패턴(KBar 연속인코딩 권장) · **MaxRet/Max5(복권효과, 음신호)**. 전부 ✅.

---

## 6. 캘린더 / 레짐 (날짜에서 무료 생성, 과적합 주의)

요일효과(약함) · 월/1월효과(약화) · **턴오브먼스(상대적 견고→우선)** · 분기말/휴장전후 · **변동성 레짐**(20d vol 분위/HMM 필터확률, 조건부 상호작용이 실익) · **추세 레짐 게이트**(ADX/RSQR 임계×모멘텀/반전) · 시장베타/상대강도 · overnight gap. 전부 ✅(HMM은 expanding refit 필수). **다중검정 보정 필수.**

---

## 7. 횡단면 전처리 (모든 피처 필수 단계)

| 변환 | 정의 | 비고 |
|---|---|---|
| **Cross-sectional rank [-1,1]** | 2·rank/(N+1)-1, 시점별 단면 | GKX 표준, 이상치 robust |
| z-score | (x-μ_t)/σ_t | 크기정보 보존 |
| ts_rank | Rank(x,d) | 종목 자기 과거 순위 |
| Sector-neutral | 섹터 내 demean/rank | ❌ 섹터 매핑 필요 |

> **반드시 KR(344) / US(512) 시장 분리 정규화** — 통화·거래일·시간대·미시구조 상이.

---

## 8. 우선순위 12 피처 (지금 OHLCV로 바로, 가장 유망)

1. **mom12m** — GKX 전모델 공통 최상위, 30년+ 강건, skip-1m, 비용게이트 양호
2. **mom1m(단기반전)** — GKX 통합중요도 단일 1위, 단 비용 최취약 → IC+비용게이트 동시측정
3. **Yang-Zhang 변동성** — CC 대비 ~14배 효율, retvol 저잡음 대체 (N∈{20,60})
4. **MaxRet/Max5(복권효과)** — GKX top-7, 음의 프리미엄, 즉시
5. **ill(Amihud)** — GKX 유동성 핵심, 호가 없이 가능, 비용게이트 직결 (volume 다중공선성 점검)
6. **High52** — George-Hwang: JT 모멘텀 ~2배 능가, 독립 예측력, GKX 94개엔 없음
7. **Corwin-Schultz/Abdi-Ranaldo 스프레드** — baspread 호가 없이 복원, 비용+유동성 양쪽
8. **overnight/intraday 수익 분해** — Lou-Polk-Skouras: 모멘텀=오버나이트·반전=인트라데이 직교분리
9. **Qlib Alpha158 KBar+롤링세트** — 100% OHLCV, GBDT/MLP 벤치마크 표준 입력(피처 뼈대)
10. **MomVolScaled** — Barroso-Santa-Clara: 크래시 거의 제거, 샤프 ~2배
11. **FIP_ID(정보이산성)** — Da-Gurun-Warachka: 연속정보 모멘텀 5.94% vs 불연속 -2.07%
12. **Qlib Alpha360 raw 시퀀스** — DL(GRU/LSTM/Transformer/TCN) 1순위 입력(5×60=300)

---

## 9. 신규 파생변수 아이디어 12 (문헌을 넘어 생성)

1. **변동성조정+정보이산성 결합 모멘텀**: MomVolScaled × FIP_ID 게이트 → 연속·저변동·강모멘텀만 증폭
2. **레짐 조건부 상호작용 텐서**: ADX/RSQR·변동성레짐 분위 × 모멘텀/반전 (추세장→모멘텀, 횡보장→반전); char×svar 크로네커
3. **오버나이트/인트라데이 변동성 점유율**: σ_o²/(σ_o²+σ_c²) — 정보비대칭 프록시, KR(갭 큼) vs US 분리
4. **Range-EWMA**: EWMA의 r²을 Yang-Zhang/GK 일별추정량으로 치환, 다중 λ(.90/.94/.97)
5. **signed jump variation 모멘텀**: realized semivariance RS⁺-RS⁻ 부호/크기 × 모멘텀 (good/bad vol)
6. **효율성비율(Kaufman) 추세품질**: \|P(t)-P(t-n)\|/Σ\|ΔP\| — FIP_ID 대안, 경로길이 정량화
7. **Amihud × 모멘텀 상호작용**: 비유동 종목에서 모멘텀 강한지(pricedelay OHLCV판)
8. **라이브러리 교차 직교화**: Alpha158/WQ101/GTJA191 중복(KMID2=Alpha#101=RSV)을 PCA/클러스터링 후 잔차만 신규피처화
9. **52주 채널위치 × 시점 정보**: PricePosition_252 × IMAX/IMIN(고점 최신성) — 앵커링+추세신선도
10. **KR/US 분리 rank 후 풀링 단일모델**: 시장별 정규화로 미시구조 흡수, 학습표본 856 전부(mom36m 표본부족 완화)
11. **Vol term-structure 레짐**: σ_YZ(5)/σ_YZ(60)·percentile-rank를 수익예측 게이트로
12. **BVC 주문불균형 누적 vs 모멘텀 다이버전스**: 가격↑인데 매수압력↓('소진') × CORR(가격-거래량) 약화 → 반전 조기경보

---

## 10. 현재 계산 불가 (데이터 추가 필요)

| 그룹 | 필요 데이터 | 난이도 | 비고 |
|---|---|---|---|
| mvel1, turn, std_turn, chcsho | **상장주식수**(분기 갱신) | 낮음 (1컬럼) | **최우선·최저비용**, GKX 통합 2위 mvel1 |
| indmom, sector-neutral, _ia | **섹터 매핑**(GICS/KRX 업종) | 낮음 | GKX top-7 산업모멘텀 |
| ep, sp, bm, cfp, dy + accounting ~60개 | **재무제표** | 중간 | 1순위 ep,sp,bm |
| char×macro 상호작용 | **거시 7변수**(svar는 자체구성) | 중간 | FRED/Welch-Goyal |
| VPIN, OFI, true Kyle λ, 정확 baspread | **호가(LOB)/체결틱** | 높음 | CS/AR 근사로 회피 |
| 정식 idiovol/ResidMom, beta 초과수익 | FF3/FF5 팩터, 무위험금리 | 중간 | 현재 CAPM/0 근사 |

→ 확보 경로·우선순위는 [ml_data_acquisition.md](ml_data_acquisition.md). **두 개의 최저비용 unlock = 상장주식수(시총·회전율) + 섹터 매핑(산업모멘텀).**

---

## 11. 다음 구현

1. **피처 계산 모듈**(`batch/features/`): OHLCV 파생 피처 순수함수(모멘텀·변동성·유동성·Alpha158 서브셋·횡단면 rank) → `stock_features_daily` 적재
2. **유용성 테스트 하니스**: Rank IC / ICIR / purged-embargo CV로 피처별 예측력 정량화(다중검정 보정) — "실제 유용한지" 게이트
3. 통과 피처로 **ML(LightGBM)** → 통과 시 **DL(Alpha360 시퀀스)**. GPU는 DL 실학습 시에만.
4. 외부 데이터(상장주식수·섹터·수급·펀더멘털) 확보되는 대로 피처 추가 후 동일 게이트 재측정.
