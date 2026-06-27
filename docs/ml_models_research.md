# 횡단면 수익예측 모델 SOTA 전수조사 — 숏리스트 + 테스트 계획

> 7개 모델군 73개 모델 조사. 자매 문서: 피처 [ml_features_research.md](ml_features_research.md), 데이터 [ml_data_acquisition.md](ml_data_acquisition.md).
>
> **과제 정의**: 단일 시계열 forecasting이 아니라 **매 거래일 종목을 미래 h일 상대수익으로 줄세우는 횡단면 alpha 랭킹**. 라벨·손실·평가지표·아키텍처 귀납편향이 forecasting과 전부 다름.

## 0. 핵심 프레이밍 (왜 forecasting SOTA 순위가 우리에게 안 통하나)
- **LTSF(장기시계열예측) 트랜스포머 순위(PatchTST>iTransformer>…)는 정보가치 거의 없음** — ETT/Weather/Traffic은 우리 과제가 아니다.
- **가장 직접적 외부 증거 = Qlib 벤치마크** (동일 지표 IC/RankIC/ICIR, 동형 데이터 Alpha158≈우리 58피처 / Alpha360≈5채널×60일 raw).
- **정직한 결론(Grinsztajn 2022·Gu-Kelly-Xiu 2020·Qlib)**: 가공 tabular는 **GBDT가 거의 모든 DL을 이김**, raw 시퀀스는 GRU/ALSTM·금융특화(MASTER/HIST) 우위. 단 CSI300 단일시장 수치 → **우리 KR+US OOS에서 purged-CV로 직접 재현·검증, 측정 없이 단정 금지.**
- **아키텍처보다 레버리지 큰 3가지**: ① 손실/타깃을 Rank IC에 직접 정렬(LambdaRankIC·listwise), ② 정직한 purged/embargo CV, ③ 시드 앙상블(저SNR 분산축소=신호추출).

## 1. 모델군별 적합성 요약

| 모델군 | 대표 | 적합성 | GPU | 판정 |
|---|---|---|---|---|
| **GBDT/tabular** | LightGBM·CatBoost·DoubleEnsemble·XGBoost | **strong** | ✗ CPU | **필수 베이스라인** |
| tabular DL | TabM·FT-Transformer·TabNet·SAINT | moderate~weak | 경량 | 앙상블 다양성 한정 |
| **시퀀스 DL** | GRU·ALSTM·TCN·Mamba | **strong** | ✓ | **DL 베이스라인** |
| LTSF 트랜스포머 | Informer·Autoformer·PatchTST·iTransformer·DLinear | weak~mismatch | ✓ | DLinear만 sanity 대조군 |
| **금융특화 DL/그래프** | **MASTER·HIST·StockMixer·GRU-PFG·TRA** | **strong** | ✓ | **진짜 프런티어** |
| 시계열 파운데이션 | Chronos·TimesFM·Moirai·MOMENT·TimeGPT | weak~mismatch | ✓ | 임베딩-as-feature ablation만 |
| **tabular 파운데이션** | **TabPFN-v2·Mitra** | moderate | 소형 | 우리 셋업에 맞는 유일 FM |
| 앙상블/적응 | 시드앙상블·LambdaRankIC·스태킹·DDG-DA·abstention | **strong** | ✗ | 아키텍처 직교, 고ROI |

**mismatch 명시**: DeepAR(자기회귀 미래값 — 수익 자기상관≈0), 시계열 FM zero-shot 직접랭킹, 채널독립 모델(DLinear/TiDE/PatchTST는 종목간 상관 구조적 결여 → per-stock 인코더로만).

## 2. 테스트 숏리스트 (5, head-to-head)

| # | 모델 | 군 | GPU | 왜 |
|---|---|---|---|---|
| 1 | **LightGBM** (+CatBoost·DoubleEnsemble) | GBDT | ✗ | 가공 tabular 최강 베이스라인. **모든 DL이 IC·Sharpe/DSR 둘 다에서 이걸 넘어야 채택**(honest gate) |
| 2 | **GRU / ALSTM** | 시퀀스 DL | ✓ | Alpha360 raw 표준. Qlib RankIC 0.058~0.060(Transformer 0.033 압도). tabular와 직교 정보 |
| 3 | **MASTER** | 금융 트랜스포머 | ✓ | **우리 과제와 동형**(intra-stock 시간 + inter-stock 횡단면 attention + 시장 게이팅). KR/US 레짐차를 market token으로 흡수 |
| 4 | **HIST** (또는 GRU-PFG) | 금융 그래프 | ✓ | 관계구조(섹터)로 공유/개별정보 분리(Qlib RankIC 0.067). GRU-PFG는 외부그래프 없이 IC~18%↑(생존편향·구축비용 없음) |
| 5 | **TabPFN-v2** (또는 Mitra) | tabular FM | 소형 | 매일 횡단면 단면(N행×58열) in-context 회귀. LightGBM과 직접 비교 + 임베딩을 GBDT 추가피처로 |

각 모델은 **LightGBM 입력=우리 58 tabular 피처**, **시퀀스 모델 입력=Alpha360식 5채널×60일 raw**. KR 모델은 여기에 **누설없는 US 컨텍스트**(아래 §4) 추가.

## 3. 테스트 계획 (운이 아니라 신호로 가린다)
1. **동일 입력/라벨 고정** — 라벨=미래 h일 상대수익의 일별 횡단면 rank/z(절대수익 아님). h 1개 고정.
2. **Purged + Embargo Walk-Forward CV** — train→purge(=h일, 라벨겹침 제거)→embargo→test. 피처는 t까지만. **point-in-time 유니버스(상폐 포함)로 생존편향 차단**.
3. **평가지표(RMSE 금지)** — 1차 일별 Rank IC→평균·ICIR; 2차 롱숏/롱온리 포트 **비용차감 Sharpe·MDD·turnover**; 과적합 **DSR 보정**.
4. **시드 앙상블 단위 비교 + 다중검정 보정** — 모델당 seed 10~20 순위평균(cherry-pick 금지). 모델 간 Rank IC 차이 Diebold-Mariano + Benjamini-Hochberg(FDR)/Bonferroni. 보정 후 LightGBM 시드앙상블 대비 유의해야 채택.
5. **손실 ablation** — MSE vs LambdaRankIC/listwise A/B(아키텍처와 직교).
6. **앙상블** — tabular(GBDT/TabPFN)+시퀀스(GRU)+횡단면(MASTER/HIST) OOF 스태킹(예측상관 모니터). abstention(고불확실 레짐 베팅축소). DDG-DA.
7. **게이트** — 어떤 DL/FM도 LightGBM 시드앙상블을 IC·Sharpe/DSR 둘 다에서 보정 후 유의하게 못 넘으면 메인 보류, 다양성 멤버로만. **CSI300 리더보드 수치는 우리 OOS 재현 전까지 불신.**

## 4. US/KR 분리 + 누설없는 US 컨텍스트 (구현됨: `batch/features/cross_market.py`)
- **US 모델 / KR 모델 2개 분리** — IC상 두 시장 정반대 거동(§ml_features_research). 통화·세션·미시구조 상이.
- **KR 모델은 US 시장데이터 추가** — 미국장 lead-lag(US 먼저 마감→KR 반응).
- **누설 방지(검증 완료)**: KR 거래일 D(00:00 UTC 개장) 직전 종료 미국장은 **US date D-1**(21:00 UTC). US(D)는 KR(D) 마감 후 → 미래. 따라서 **as-of backward join, US date 엄격히 < KR date**(`allow_exact_matches=False`). 실측 검증: 565K행 leak_rows=0, min_gap=1일.
- US 컨텍스트 피처: 시장집계(`usx_mkt_ret/ret5/ret21/vol21/breadth`, 한 날짜 전종목 동일→횡단면 단변량 IC≈0이나 **트리/NN 상호작용·레짐**에 기여) + **종목별 US 민감도**(`usx_beta/usx_corr`, lead-lag 롤링베타 — 횡단면 변동).

## 5. GPU 판정 — **필요(True)**
숏리스트 5 중 **4개(GRU·MASTER·HIST·TabPFN)가 GPU 필요**, LightGBM/CatBoost만 CPU. 사용자 방침("DL 실제 쓰면 GPU")에 따라 **GCP 온디맨드 GPU VM**(T4/L4 단일 충분) 구성 정당화. 기동→학습→모델 아티팩트(GCS)→자가종료(기존 온디맨드 패턴 확장). 트리 베이스라인은 CPU 선행 → DL 트랙 진입 시 GPU 기동.

## 6. 함정 (정직)
- **룩어헤드/누수**: 라벨 미래 h일 → purge(≥h)+embargo 없으면 IC 부풀림. 피처 point-in-time만.
- **생존편향**: 사후 856종목만으로 학습=알파 과대. 상폐·편입탈락을 시점 유니버스에 포함(미해결 과제).
- **저SNR 과적합**: 단일 IC 현실치 0.02~0.05. 시드 cherry-pick=운을 실력으로 착각 → 시드앙상블 평균 필수.
- **IC↑ ≠ 수익↑**: XGBoost가 LSTM보다 IC 낮아도 Sharpe 높은 사례(QuantBench). Rank IC와 백테스트 Sharpe/DSR 함께 본다.
- **비정상성/레짐**: 단일분할 평가는 레짐 운. walk-forward 다폴드+ICIR. 레짐변화엔 abstention(베팅축소)이 모델교체보다 효과적.
- **외부 벤치마크 맹신 금지**: 인용 SOTA는 CSI300 단일시장·단일시드. 유일한 권위는 우리 purged-CV Rank IC/ICIR/DSR.
- **KR/US 혼합 오류**: 한 cross-section에 섞으면 랭킹 오염 → 시장별 분리/market token.

## 7. 모델 결합(앙상블·MoE) 전략 — 41기법 조사

### MoE가 우리 케이스에 맞나 — **아니다(부분만)**
핵심 오해 정정: 금융 SOTA MoE(MIGA·TRA·Switch/Expert-Choice)는 거의 전부 **"한 학습형 네트워크 안에 동질 내부 전문가 + 라우터를 end-to-end 학습"**이지, **"이미 학습된 5개 이질 외부 모델을 expert로 꽂고 게이팅을 학습"이 아니다**(후자는 사실상 stacking). MIGA류 성공은 대표본·다피처·중국시장 특수성(63전문가·10.5년·626피처) 의존 → 우리 저SNR·소표본에선 **전문가붕괴·과적합**으로 ROI 음(-). **TRA는 결합기가 아니라 백본 내부 모듈**(오인 금지). **학습형 입력조건부 게이팅 = 비권고**(자유도 폭증, 사용자가 우려한 그 과적합).
→ **우리에게 실익 있는 유일한 MoE = 게이트를 '학습'하지 않고 관측 레짐(시장·변동성)으로 하드 분기**. **KR/US 분리가 바로 그 가장 안전한 '시장 레짐 MoE'이고 이미 채택됨.** 그 위 변동성 거친 버킷(≤3~4, 분위 사전고정)만 선택적.

### 결합 숏리스트 (저위험 순, must-beat = 동일가중)
| 기법 | 역할 | 과적합 |
|---|---|---|
| **① 시드/스냅샷 앙상블 + SWA** | 결합 *이전* 모델 내부 분산축소. 저SNR서 DL run-to-run 변동이 신호보다 큼 → **자유도 0의 무료 점심·전제** | low |
| **② 횡단면 rank 평균 동일가중** | 5모델 결합 **default·must-beat 기준선**. forecast combination puzzle의 정확한 수혜(가중 추정오차>분산축소 이득). z보다 **rank**가 이질 출력·꼬리에 강건 | low |
| **③ 풀드 ICIR 가중 + 강수축(NNLS 합1·비음)** | 챌린저. IC격차가 CV서 일관될 때만. λ 1개·시장공통·정적, 1-SE로 동일가중 선호 | medium |
| **④ Abstention / 레짐 게이트(이산 tail-cap)** | **"어떻게 합치나"보다 "언제 안 거래하나"**가 Sharpe 기여 큼. 5모델 disagreement=공짜 레짐신호. 연속 역가중 금지(불확실성↔신호크기 ρ≈0.6) | medium |
| **⑤ 관측 레짐 하드분기(rule-based MoE)** | KR/US(채택) + 변동성 버킷. **게이트 학습 금지, 관측변수로 하드코딩** | low~med |

**비권고(저SNR서 과적합)**: 학습형 sparse/dense MoE 게이팅, 비선형/딥 스태킹 메타러너, 온라인 가중(Hedge/EWA — 극소 엣지 잠식), 시간가변 가중, 직교화(이질이라 제거할 중복 적음), DDG-DA(결합기 아님·비단조).

### 결합 적용 파이프라인 (결합도 다중검정 대상)
1. **분산축소(결합 前)**: 5모델 각 시드 10~20(+DL SWA) → day별 평균 → 시장별·횡단면 rank 표준화 → 결합기 입력 5채널(raw 예측 직접투입 금지).
2. **OOF 생성**: 학습형 결합은 base OOF를 **purged k-fold + embargo(라벨 horizon만큼), 폴드는 '날짜' 단위**(종목 단위=같은날 누설). 가능하면 CPCV로 PBO 추정.
3. **결합 적합·비교**: default 동일가중(자유도0, OOF 불요). 챌린저(③ 또는 Caruana 택1)는 OOF로, 전구간 단일가중.
4. **채택 게이트(DSR)**: 시도한 모든 결합변형을 시행수로 세어 DSR 디플레이트. ΔICIR이 동일가중을 유의하게 못 이기면 **동일가중 폴백**. 복잡도 1단위↑ = DSR 패널티↑.
5. **Abstention(배포 안전장치)**: 채택 신호 위에 trailing RankIC·disagreement 이산 게이트 + tail-cap. 임계는 DEV에서만.

> **요지**: 우리 이질 5모델은 상관이 낮아 **단순 동일가중(rank 평균)만으로 결합 이득**을 얻을 가능성이 크다. 화려한 학습형 결합/딥 MoE는 자유도가 곧 DSR 손실 — **측정으로 동일가중을 이길 때만** 채택.

## 8. 다음 구현 순서
1. **공유 학습 인프라**: 라벨 생성(횡단면 rank) + purged/embargo CV 분할기 + Rank IC/ICIR/DSR 평가 + 시드앙상블 — 모든 모델 공용.
2. **LightGBM 베이스라인**(CPU): US/KR 분리, KR은 US 컨텍스트 포함, lambdarank, 시드앙상블 → 게이트 기준선 확정.
3. **GPU VM 구성** → GRU → MASTER → HIST/GRU-PFG → TabPFN 순 head-to-head(동일 CV/지표).
4. 스태킹·abstention·DDG-DA로 견고화. 통과 모델만 배포 검토(생존편향·비용 게이트 후).
