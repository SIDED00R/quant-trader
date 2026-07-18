# TODO — 매매 알고리즘 고도화 (앙상블) + 주식(KR/US) 확장

> 순차 진행. 한 항목 끝나면 체크하고 다음으로. 세션 종료 시 "TODO.md 업데이트해줘"로 진행상황 반영.
> 원칙: 각 단계는 백테스트/회귀로 **검증 가능한 성공 기준**을 갖는다.

> **📌 현재 운영/배포 상태 (2026-07-05)**: **코인 앙상블 + KR/US 주식 ML 라이브 배포(모의) — 2-VM 온디맨드**.
> - **아키텍처**: 수집 VM(상시, e2-small, `--profile collector` 수집·저장만) + **매매 VM(온디맨드·자기완결 로컬 DB, Cloud Scheduler 8잡: 코인 매일 01:00 UTC·KR 15:00 KST·US 15:00 ET 장마감 전·월간 유지보수 첫 토요일, 기동→동기 배치→자가종료)**. 라이브 매매 = trade_once/stock_trade_once/us_trade_once. Kafka는 데이터 팬아웃만. **상시 비용 ~$13/월**.
> - 대시보드 = 매매 VM 온디맨드 모드(`https://jh-quantlab.duckdns.org` 구글 OAuth, SSH 터널 폴백). 모델 출처 = `docs/model.md`. 상세 = `DEPLOY.md` 상단.
> - 계정 초기화 완료(본인 2계정 → ₩10M). 주식은 KIS 모의계좌(KR+US 통합).
> - **⏳ 운영 후속 (2026-07-17, #266)**: 매매 VM을 `us-central1-a` → 서울 `asia-northeast3-a`/`e2-standard-2`로 이전 완료(존 용량 고갈 회피). **[내일 할 일] us-central1 백업 자원 삭제** — 서울 첫 실스케줄 매매 성공 확인 후:
>   - 고아 디스크 `coin-trade-vm`·`coin-trade-vm-qxl1` (us-central1-a, 각 20GB)
>   - 스냅샷 `coin-trade-vm-premigrate` · 머신이미지 `coin-trade-vm-mi`
>   - `gcloud compute disks delete coin-trade-vm coin-trade-vm-qxl1 --zone=us-central1-a`; `gcloud compute snapshots delete coin-trade-vm-premigrate`; `gcloud compute machine-images delete coin-trade-vm-mi` (월 ~$3-4 절감)

## 0단계 — 토대: 백테스트 & 성과측정 하니스  (#46 / PR #47)
- [x] ClickHouse 틱 replay 백테스트 엔진 (`backtest/datasource.py` + `engine.py` + `run.py`) — 기간/심볼 지정 replay
- [x] 가상 체결·수수료·슬리피지 모델 (`backtest/fills.py` + `account.py`, 라이브 engine/portfolio와 동일 가정)
- [x] 성과지표 모듈 (`backtest/metrics.py`): 누적수익률·승률·MDD·Sharpe·손익비·거래수·평균손익
- [x] 결과 리포트(표/CSV) + 재현 메타 (`backtest/report.py`, run_meta.json)
- [x] **현 SMA 전략 baseline 측정** → 2년·5종목 1분봉: **누적 −97.97%, 거래 46,212건, 수수료 8.3M(자본 83%)** — 과매매·수수료가 주범 (`docs/baseline.md`)

## 1단계 — 전략 추상화 (플러그인 구조)  (#48 / PR #49)
- [x] `strategy/base.py`: `Strategy` ABC + `Broker`/`MarketTick` 프로토콜 (신호+사이징+청산을 on_tick에 캡슐화)
- [x] SMA 로직을 `strategy/sma.py`의 `SMAStrategy`로 추출 (sma_trader 순수 함수 재사용, Kafka/DB 비의존)
- [x] 백테스트 회귀: 추출 전 골든 동일 재현 + 청산 4경로(STOP/TAKE/TRAIL/DEADCROSS) 가드 (무행동 변경 증명)
- [x] 전략 레지스트리(`strategy/registry.py`) + 백테스트 `--strategy`(레지스트리·`--strategy`는 백테스트 전용 — 라이브는 고정 앙상블 `LiveEnsemble`(부하 specs 하드코딩 `_DEFAULT_SPECS`), `ENSEMBLE_SYMBOLS`는 종목 유니버스)
- 참고: 라이브 `sma_trader.py`는 무수정(채택은 4~5단계). 백테스트는 `SMAStrategy` 채택 → `backtest/strategy.py` 삭제(중복 제거).

## 1.5단계 — 거래빈도·수수료 제어 (baseline 진단 반영)  (#52)
> baseline(−97.97%)의 주범은 신호가 아니라 과매매·수수료(자본의 83%). 신규 알고리즘 전에 공통 레버부터.
- [x] 시간 기반 가드(쿨다운/최소보유/워밍업)를 **봉 단위**로 재튜닝 — 60/30/25봉(config 기본값, env 오버라이드)
- [x] 수수료 인지 필터(`STRATEGY_MIN_EDGE_PCT`, 기본 0.5%)로 약신호 진입 차단
- [x] 데드크로스 청산 재검토 → 토글화(`STRATEGY_DEADCROSS_EXIT`, 기본 off; on/off 백테스트 비교)
- [x] 재튜닝 후 SMA baseline 재측정 → **공정 기준선 −62.32%, 거래 5,937건(−87%), 수수료 3.9M(−53%)** (`docs/baseline.md`)

## 2단계 — 후보 알고리즘 분석 & 구현  (#54)
- [x] 대중적 투자 알고리즘 조사 정리 (RSI / MACD / 볼린저밴드 / 돌파·모멘텀 / 평균회귀) → `docs/algorithms.md`
- [x] RSI 전략 구현 (`strategy/rsi.py`)
- [x] MACD 전략 구현 (`strategy/macd.py`)
- [x] 볼린저밴드 전략 구현 (`strategy/bollinger.py`)
- [x] 돌파/모멘텀 전략 구현 (`strategy/breakout.py`)
- [x] 공통 규율 베이스(`strategy/disciplined.py`) + 지표 순수함수(`strategy/indicators.py`) + 레지스트리 등록 + 단위/엔진 테스트
- [ ] (선택) 평균회귀 등 추가 전략 — 필요 시 동일 `DisciplinedStrategy` 패턴으로 확장

## 3단계 — 검증 & 저회전 양수수익 전환 (재정의: 딥리서치 #59 기반)
> 일괄 백테스트 결과 **전 전략 음수**(SMA −62%가 최선, 후보 RSI/MACD/볼린저/돌파 −89~−99%; 과매매·수수료가 주범).
> 딥리서치(검증 13건) 결론 = **타임프레임 상향 + 추세 길게 보유 + 변동성 타게팅**.
> 목표: **수수료 차감 후 누적수익 양(+)**. 검증은 전체데이터 일괄 금지 → train/test·walk-forward + Deflated Sharpe.
- [x] 전 알고리즘 일괄 백테스트(2년·5종목·동일자금) — 결과 위 (`runs/stage3_*`)

### 3.1 백테스트 하니스 보강 (#9) ✅
- [x] 상위 타임프레임 리샘플링(`--bar-min`, 예 일봉=1440) — `upbit_candles._resample`(종목별 merge 전, 전역순 보존)
- [x] walk-forward(롤링 IS/OOS) 러너 — `backtest/walkforward.py`. NullBroker priming으로 prime 무거래 보장(룩어헤드 차단)
- [x] **Deflated Sharpe**(시도 수 N 보정) — `metrics.deflated_sharpe`(Bailey & López de Prado 2014)

### 3.2 저회전 전략 설계·구현 (#10) ✅
- [x] **추세추종 long-or-cash**: 일봉 단기/장기 SMA, 상향 보유·하향/극단변동성 현금화(공매도X) — `strategy/trend.py`
- [x] **변동성 타게팅 사이징**: 진입 비중 = min(상한, 목표변동성/실현변동성). 추세 유지 중 무매매(저회전)
- [x] **레짐·변동성 필터**: 연율 실현변동성 상한 초과 시 진입 차단·강제 현금
- [x] **비용 인지**: 수수료 양자화 여유분 예약 사이징 + 추세 반전 청산(왕복비용 ≪ 추세 포착폭). sma_trader/kafka 비의존
- [ ] (선택·보류) 청산 임계값 OU Monte-Carlo — 현 추세반전 청산으로 충분, 필요 시 후속

### 데이터 인프라 — ClickHouse 통일 + 장기 표본 (#11 / #12)
- [x] 업비트 일봉 장기 백필 → `candles_1d`(BTC/ETH/XRP 2019-11~, 6.6년) — `backtest/backfill_daily.py`(`--all-krw` 전체 마켓 지원)
- [x] datasource `--ch-table`(candles_1m/1d) + run/walkforward `--source clickhouse` 연동
- [x] CSV 분봉 캐시 → `candles_1m` 적재(523만 행) — 저장소 ClickHouse 통일

### 3.3 유니버스 선정 ✅ — **BTC/ETH 확정**
- [x] 측정: BTC만 > BTC/ETH > 5종목. 알트(DOGE/SOL) 과매매·손실 주범 → 운용 = **BTC + ETH**

### 3.4 선별 ✅ — **엄격 기준 충족** (성공기준: OOS 양수 AND Deflated Sharpe ≥ 0.90)
- [x] 채택 파라미터 = **고정 5/40**(per-fold 그리드 최적화는 과적합으로 OOS 더 나쁨)
- [x] 6.6년 표본 충족: 그리드 DSR 0.997 / 고정 5/40 PSR 1.000, OOS Sharpe 1.37~1.51 (`docs/baseline.md`)
- [x] 교훈: 2년에선 DSR<0.95(미달) → **표본 길이(T)가 병목**, 장기 데이터로 해소
- [x] 전략 보강: 변동성 타게팅 리밸런싱(`TREND_REBALANCE_BAND`) — Sharpe 소폭↑, MDD는 추세 본질이라 미개선(기본 off)

> 리서치 근거(검증 13건, 출처 20): TS momentum>cross-sectional · 추세 초과수익=하락회피 · 변동성타게팅 Sharpe 1.12→1.42 · 10/40일 SMA 10년 walk-forward(Sharpe 0.5~1.5) · naive sign-trading은 10bps에서 사망 · Deflated Sharpe로 다중시도 보정. (보류: 임포트 분리·중복통합·MACD 래치 = 코드리뷰 #57 deferred, 본 재설계와 함께)

## 4단계 — 지휘관(Commander) 앙상블
### 백테스트 앙상블 ✅ — 채택(일관성 우선)
- [x] `strategy/ensemble.py`: 다중 추세속도 목표비중 가중합 → 합성 목표 주문(Commander 백테스트 구현)
- [x] `strategy/trend_signal.py`: 추세 결정 코어(래치) 분리 — 실행과 신호 분리, 앙상블 재사용
- [x] 채택안 = **5/40·10/60·20/100 + band 0.5**(BTC/ETH 6.6년: OOS Sharpe 1.45·양수 65%, 종목별 교차검증 강건)
- [x] 단일 SMA baseline(−62%) 대비 대폭 개선 + 단일 trend 대비 일관성↑(양수 fold 50%→65%)

### 라이브 배선 (#61) ✅ 배포됨
- [x] 신호 스키마/토픽 + `common/schemas.Signal` + config TOPIC_SIGNALS/ENSEMBLE_SYMBOLS
- [x] 앙상블 신호 워커 `strategy/live_ensemble.py`(candles_1d 워밍업 → 일봉 마감 신호 발행)
- [x] `strategy/commander.py`: `strategy.signals` → 목표비중 모의주문(place_order, 실거래 아님)
- [x] docker-compose 서비스화(ensemble-signals/commander, sma_trader 교체) + GCE VM 배포(공개 대시보드)
- [x] 대시보드 리디자인(터미널·라이트/다크·앙상블 스탠스·순손익(수수료반영)) + `/strategy/ensemble`
- [x] `aggregator/daily.py`: candles_1m → candles_1d 일일 최신화(대시보드 스탠스 freshness)
- [x] 계정 초기화(`scripts/reset_account.py`) + 모델 출처 문서(`docs/model.md`)
- [ ] 모의매매 라이브 검증(일정 기간 — 현재 CASH, 추세 진입 시 commander 동작 확인)

## 5단계 — 다부하 Commander + 적응형 가중치 + 오케스트레이션 (의존성 순서)
> 목표: 단일 앙상블 → **전략 여럿을 성과로 저울질하는 진짜 Commander**. 단 채택 앙상블은 고정 파라미터라
> 적응형 가중치는 과적합으로 성과를 *악화*시킬 수 있음(walk-forward에서 그리드<고정 입증) →
> **적응층 기본 off·DSR 게이트로 격리**해 효과 측정 후 켠다("올바른 순서로 전부 짓되 위험한 한 층만 toggle").
- [x] **5.1 부하 분리**: 3 trend 속도(`TrendSignal`)가 `strategy.signals`에 **전략명 태그**로 발행(`live_ensemble` 다부하 확장) #68 #69
- [x] **5.2 가중치 저장소**: Postgres `strategy_weights(strategy, weight, updated_at)` + `load_weights`(미등록/합0 → 동일가중 폴백) #68 #69
- [x] **5.3 commander 적응형 합의**: 봉별 버퍼링 → `strategy_weights` 가중합(`combined_for_bar`), **적응 toggle `ENSEMBLE_ADAPTIVE` 기본 off=동일가중=부하 평균**(현 동작 보존) #68 #69 — VM 배포·검증 완료(2026-06-20)
- [x] **5.4 부하별 성과추적 + 재평가 잡**: `reeval_weights`가 각 부하 OOS 성과(`walkforward.oos_returns`/DSR) → `weight_policy.compute_weights`(floor·cap·EWMA·**demote≠delete**·DSR게이트) → `strategy_weights` UPSERT. 별도 배치 이미지(`Dockerfile.batch`). #71 #72 #73 #74
- [x] **5.5 스케줄러 + 온디맨드 인프라**: 단일 16GB → **2-VM 분리**(상시 수집 + 온디맨드 매매) + **Cloud Scheduler**(매일 01:00 UTC 매매 VM 기동 → `trade_once` 동기 배치 → 자가종료). **Airflow는 보류**(무거운 정기 잡 없음 → DL 도입 시 도입). 비용 ~$66→~$13/월(현행). #75~#87
- [~] **5.6 병렬성**: Kafka 다소비자 팬아웃(ingester→sink+candle)으로 데이터 경로는 이미 충족. 부하 합성은 `trade_once`가 동기 처리(온디맨드 배치라 N개 독립 컨슈머 불요 — 설계 변경으로 대체)
> ⚠️ **가드(절대)**: 적응층 기본 off · DSR 게이트 · EWMA/일변동 캡 · demote≠delete · 가중식 자체 OOS 검증.
> "재학습으로 향상"이 아니라 "**열화 부하 자동 강등**" 안전장치로 보수적 사용.
> 검증: 적응 on/off A/B(동일 OOS) · Cloud Scheduler 잡 수동 트리거 성공 + `strategy_weights` 갱신 + `trade_once` 반영 확인.

## 6단계 — 코인 마무리 & 성과 검증
- [ ] 라이브 모의매매로 앙상블 vs 단일 SMA 성과 비교 (일정 기간 — **실제 시장 경과 필요**, 현재 CASH)
- [x] 대시보드 성과 패널 — 실현손익·승률·거래수·수수료(`/performance` FIFO, `api/web`) #61
- [x] 코인 단계 회고/문서화 (아래 회고 + `docs/model.md` 모델카드) #61

### 코인 1차 라이브 완료 회고 (2026-06-20, 구 BACKLOG.md에서 이관)

**달성**: 과매매로 −62%(1분봉 SMA)였던 전략을 **일봉 추세 앙상블(저회전)** 로 재설계해 walk-forward에서
유의한 OOS(Sharpe~1.47/PSR 1.0)까지 끌어올리고 **GCE VM에 라이브(모의) 배포**. 모델 출처 = `docs/model.md`.

**핵심 교훈**
- **과매매·수수료가 주범**(신호보다 타임프레임). 1분봉→일봉으로 거래 −99%, 수수료 자본 39%→2%대.
- **표본 길이(T)가 유의성 병목** — 2년 DSR<0.95, 6.6년이라야 충족. 데이터 확보가 튜닝보다 효과적.
- **per-fold 그리드 최적화=과적합** → 고정 파라미터 + 앙상블로 파라미터 리스크 분산.
- **적대적 코드리뷰가 실수를 잡음** — walk-forward prime 오염(거짓 OOS −31.5%), bollinger σ=0 오매수, 부분매도 수수료 중복, 프로덕션의 backtest 의존(실배포 크래시).

**남은 것(시간·인프라 의존)**
- 라이브 모의 앙상블 vs 단일 SMA **실측 성과 비교** — 실제 시장 경과 필요(현재 CASH).
- ~~5단계 Airflow 배치 DAG~~ — **폐기**(Cloud Scheduler 온디맨드로 대체). 채택 앙상블은 **고정 파라미터**라 "가중치 자동 재학습 루프" 자체가 현 설계엔 불요.
- 스트리밍 경로(로컬 dev 전용 `disciplined` 계열)의 재시작 시 진입시각/쿨다운 복원은 인메모리 한계로 남음 — 라이브는 일배치(`trade_once`)라 무관.
- 구 백로그의 "계정별 전략 선택 UI"·"잔고 비율 포지션 사이징"은 **폐기** — 라이브가 고정 앙상블 목표비중(+변동성 타게팅) 설계로 대체되어 불요.

> 참고: 모의 자금 기준이며 수익 최적화가 목표가 아니라 Kafka 파이프라인 위에서 전략을 붙여보는 학습이 목적이다.

## 7단계 — 주식 토대
> **완결** — 체결/계좌 모델·틱 수집기 완료. 키움 기반 매매 계획(단건 주문 왕복·FID 보정·유니버스 선정)은 **폐기**: 체결=KIS(`common/broker/kis_*`), 유니버스=ML 동적 top-N.
- [x] 키움 API 조사: REST/WebSocket API + 모의투자 계정 발급·인증 흐름 → `docs/kiwoom.md` (#102)
- [x] `stock_ingester`: 키움 실시간 시세 → 신규 토픽 `stock.ticks` (코인 ingester 패턴 재사용) (#104 — kiwoom_client(토큰)+stock_kiwoom(WS LOGIN/REG/0B/PING echo)+stock_tick_clickhouse 싱크+config/compose/clickhouse. 실서버(모의) 검증 완료 — **아카이브 수집 전용으로 운영**, 매매 활용 계획은 폐기)
- [x] 주식 체결/계좌 모델 (#120/PR#121): ①정수단위(ROUND_DOWN+<1주 skip 로그)를 `backtest/engine` 입구에(`_adjust_qty`) ②신규 `common/marketdata/market_hours.py`(`asset_class`/`is_stock`/`is_market_open` — 코인 항상 True, 국내주식 KRX 09:00–15:30 KST) ③매도 거래세 비대칭(`STOCK_SELL_TAX_RATE` 0.20%, **국내주식만** — 미국·코인=0, `backtest/fills.tax`+`account.apply_sell` proceeds−fee−tax, `ClosedTrade.sell_tax`). **라이브-백테스트 수학 미러링 계약 준수**, 코인 경로 무영향(회귀 테스트 고정), 단위테스트 15개.

## 8단계 — 주식 백테스트·라이브 (ML 스코어러로 전환)
- [x] 주식용 백테스트 하니스 지원 (#122: `datasource`/`run.py --ch-table`·`metrics.total_tax`+리포트 매도세; #124: `walkforward --ch-table stock_candles_1d`+OOS 매도세 집계) — 기준치 선별·유니버스는 ML 트랙(GBDT 챔피언·동적 top-N)으로 대체
- [x] 주식 모의매매 라이브 가동 — ML 챔피언 주간 리밸런싱(KIS KR/US), 성과는 운용 관찰 중

### 8.1 인트라데이(분봉) 매매 연구·검증 (신규 트랙 — `docs/intraday_research.md`)
> 일 1회 → 분 단위(1~60분) 고빈도 탐색. 유니버스 KOSPI200+KOSDAQ150+S&P500+NASDAQ(~950, long-or-cash). 연구→검증→조건부배포, ML/DL 포함(GPU는 DL/RL 학습 시에만).
- [x] **단계1 전수조사 (#126)** → `docs/intraday_research.md`. shortlist: ①변동성 타게팅 ②횡단면 종가직전 리버설(롱) ③횡단면 모멘텀/상대강도(롱) ④필터링 돌파(ORB) ⑤트리 ML(LightGBM). 제외: 단기반전 단독·페어(long-only破)·마이크로구조(호가 필요)·DL/RL 배포(연구트랙). 핵심: 비용(KR 매도세 0.20%)·생존편향·다중검정이 방법보다 중요.
- [x] 단계0 분봉 데이터(#128 스키마·#130 토스 분봉 수집기·게이트 통과 1m KR+US 3년+) + 정합성 하니스(#132 US세션/연율화/정규장필터)
- [x] 단계2 shortlist 구현: 횡단면 엔진 `xs_reversal`/`xs_momentum`(#134) + 세션 `orb`/`intraday_momentum`(#136). 트리 ML은 미착수(후속)
- [x] 단계3 검증(#138 일반화 walk-forward) → **1차 실측 결과 `docs/intraday_baseline.md`**: 1분봉 전멸(−100%, 비용사), **일봉 횡단면 모멘텀(lb60) OOS +116%·PSR 0.99 게이트 통과**. 빈도가 결정변수.
- [x] 단계3b 채택후보(xs_momentum 일봉) **전 유니버스 재검증** — 856종목·7.1y 통과(#143/PR#144, DSR≥0.99) → 이후 ML 트랙(GBDT 챔피언)으로 발전
- [x] 단계4 배포 — ML 챔피언(GBDT) 주간 리밸런싱으로 실현(trade_once류 일배치·KIS 체결)

## 9단계 — 통합 자산배분
- [ ] 전체 자산 100 기준 코인+주식 배분 정책 설계 (자산군 비중·리밸런싱)
- [ ] 통합 대시보드 (코인+주식 합산 평가자산/손익)
- [ ] 통합 운영 회고/문서화

## ML 피처·외부데이터 트랙 (상세: `docs/ml_progress.md` §1~7)
> 주식 횡단면 수익예측. **모델=GBDT(LightGBM lambdarank)** — DL(GRU/MLP) 비교서 채택(트리 압승, §4). 평가=purged walk-forward + Rank IC/ICIR/NW-t + 롱숏 Sharpe, US/KR 분리.
- [x] OHLCV 피처(58)·GBDT 베이스라인·DL 비교·HP 튜닝 (#145~148)
- [x] US 외부데이터 ablation → **OHLCV+펀더+13F+섹터 채택**(3.78%/NW_t2.3)
- [x] **KR 외부데이터 수집·배선·OOS 검증 (2026-06-29 완료)**:
  - [x] KRX 수급·공매도·외국인보유 수집기 `krx.py` + 고속 `krx_bulk.py`(by-date) — #150 / **PR #151**
  - [x] KOSPI200·KOSDAQ150 PIT 멤버십 `kr_index_membership.py` — #152 / **PR #153**
  - [x] DART 펀더멘털 `kr_fundamentals.py`(fundamentals_quarterly plug-in) — #154 / **PR #155**(#151 스택)
  - [x] 피처 배선 `kr_microstructure.py` + `dataset.py` KR경로 + IC/ablation — #156 / **PR #157**
  - [x] 데이터 적재: KR 미시구조 **2019-05~2026-06 (7년)·344종목**(수급 157만행), DART 펀더 344종목
  - **견고 결론**: KR OHLCV 0.26% → +펀더+미시 **1.21% OOF Rank IC, LS_Sharpe 1.03→1.34**. 방향 일관 양(+)이나 **NW_t~1.0(강≥2 미달)** — KR은 구조적 약신호(US 3.4~3.8% 대비). 공매도 단변량 −7.4%는 full-sample·금지레짐 효과.
- [x] **PR 4개 리뷰·머지** — 스택 순서 **#151 → #155**, #153·#157 독립. 머지 후: ①공용 KRX 세션헬퍼(`_krx_session.py`)로 중복 통합 ②README `batch/rawdata` 목록 reconcile(#200에서 완료).
- [x] **공매도 금지 레짐 분리 검증** — 신호 실재하나 챔피언(OHLCV+DART)에 흡수·증분 0 → **보류 확정** (`docs/ml_progress.md` §7 task2).
- [x] KR 챔피언 피처셋 확정 = **OHLCV+DART**(macro·미시 제외, 1.34%/NW_t 1.1) + `baseline_lgbm KR` 기본 반영(플래그 없음=챔피언) (`docs/ml_progress.md` task3a).
- [ ] (후속) **생존편향 통제**(탈락·상폐 종목 가격 적재) — 절대 IC 과대의 본질, 별도 트랙.
