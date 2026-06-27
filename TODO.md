# TODO — 매매 알고리즘 고도화 (앙상블) + 주식(키움) 확장

> 순차 진행. 한 항목 끝나면 체크하고 다음으로. 세션 종료 시 "TODO.md 업데이트해줘"로 진행상황 반영.
> 원칙: 각 단계는 백테스트/회귀로 **검증 가능한 성공 기준**을 갖는다.

> **📌 현재 운영/배포 상태 (2026-06-20)**: **앙상블 라이브 배포 완료(모의) — 2-VM 온디맨드**.
> - **아키텍처**: 데이터 VM(상시, e2-medium 4GB, `--profile data` 수집·저장·대시보드) + **매매 VM(온디맨드, Cloud Scheduler 매일 01:00 UTC 기동→`trade_once` 동기 배치→자가종료)**. 라이브 매매 = trade_once(스트리밍 commander 아님). Kafka는 데이터 팬아웃만. **상시 비용 ~$66→~$25/월**.
> - 공개 대시보드 `https://jh-coinlab.duckdns.org`(Basic Auth). 모델 출처 = `docs/model.md`. 상세 = `DEPLOY.md` 상단.
> - 계정 초기화 완료(본인 2계정 → ₩10M). 현재 앙상블 CASH(추세 미진입).

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
- [x] 전략 레지스트리(`strategy/registry.py`) + `ACTIVE_STRATEGIES` env + 백테스트 `--strategy`
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
- [x] **5.5 스케줄러 + 온디맨드 인프라**: 단일 16GB → **2-VM 분리**(상시 데이터 `--profile data` + 온디맨드 매매) + **Cloud Scheduler**(매일 01:00 UTC 매매 VM 기동 → `trade_once` 동기 배치 → 자가종료). **Airflow는 보류**(무거운 정기 잡 없음 → DL 도입 시 도입). 비용 ~$66→~$25/월. #75~#87
- [~] **5.6 병렬성**: Kafka 다소비자 팬아웃(ingester→sink+candle)으로 데이터 경로는 이미 충족. 부하 합성은 `trade_once`가 동기 처리(온디맨드 배치라 N개 독립 컨슈머 불요 — 설계 변경으로 대체)
> ⚠️ **가드(절대)**: 적응층 기본 off · DSR 게이트 · EWMA/일변동 캡 · demote≠delete · 가중식 자체 OOS 검증.
> "재학습으로 향상"이 아니라 "**열화 부하 자동 강등**" 안전장치로 보수적 사용.
> 검증: 적응 on/off A/B(동일 OOS) · Cloud Scheduler 잡 수동 트리거 성공 + `strategy_weights` 갱신 + `trade_once` 반영 확인.

## 6단계 — 코인 마무리 & 성과 검증
- [ ] 라이브 모의매매로 앙상블 vs 단일 SMA 성과 비교 (일정 기간 — **실제 시장 경과 필요**, 현재 CASH)
- [x] 대시보드 성과 패널 — 실현손익·승률·거래수·수수료(`/performance` FIFO, `api/web`) #61
- [x] 코인 단계 회고/문서화 (`BACKLOG.md` 회고 + `docs/model.md` 모델카드) #61

## 7단계 — 주식(키움) 토대
> **진행**: ① #4 체결/계좌 모델 ✅(#120/PR#121) → ② 정규장(09:00~15:30 KST)에 #2 라이브 틱 검증 + FID 보정(대기) → ③ #5 단건 주문 왕복(KIS 주문 PR#117 평일 체결검증 대기). (#3 유니버스는 8단계 백테스트 후 결정·보류).
- [x] 키움 API 조사: REST/WebSocket API + 모의투자 계정 발급·인증 흐름 → `docs/kiwoom.md` (#102)
- [~] `stock_ingester`: 키움 실시간 시세 → 신규 토픽 `stock.ticks` (코인 ingester 패턴 재사용) (#104 — kiwoom_client(토큰)+stock_kiwoom(WS LOGIN/REG/0B/PING echo)+stock_tick_clickhouse 싱크+config/compose/clickhouse. **토큰·WS·LOGIN·REG 실서버(모의) 검증 완료** — 정규장 0B 틱 수신·FID(10/15/20) 보정만 대기)
- [ ] 종목 유니버스 선정 논의/결정 (전 종목 X — 어떤 종목 대상으로 할지) → `docs/kiwoom.md` (잠정 005930+000660, 확정은 8단계 백테스트 성과로)
- [x] 주식 체결/계좌 모델 (#120/PR#121): ①정수단위(ROUND_DOWN+<1주 skip 로그)를 `backtest/engine` 입구에(`_adjust_qty`) ②신규 `common/market_hours.py`(`asset_class`/`is_coin`/`is_stock`/`is_market_open` — 코인 항상 True, 국내주식 KRX 09:00–15:30 KST, 미국주식 미지원) ③매도 거래세 비대칭(`STOCK_SELL_TAX_RATE` 0.20%, **국내주식만** — 미국·코인=0, `backtest/fills.tax`+`account.apply_sell` proceeds−fee−tax, `ClosedTrade.sell_tax`). **라이브-백테스트 수학 미러링 계약 준수**, 코인 경로 무영향(회귀 테스트 고정), 단위테스트 15개. *라이브 `place_order` 정수/장시간 가드는 #5로 이월.*
- [ ] 키움 모의계정으로 단건 주문 왕복 검증 (API → 모의체결 → 내 대시보드 반영) — **정규장+모의계좌 필요**: 코인 Kafka 매칭엔진 **우회**. `api/routes/stock_orders.py`(kt10000 매수 1주) + `kiwoom_client` 주문 호출 + `executions`에 `asset_class`/`broker_order_id` 컬럼(ADD COLUMN IF NOT EXISTS) + 대시보드 주식 패널(`loadStockExecs`). 체결멱등 `uuid5(ord_no)`

## 8단계 — 주식 앙상블 (구조 재사용)
- [ ] 코인 `Strategy`/`Commander`/신호버스 구조를 주식에 재사용 (`stock.signals` 토픽)
- [~] 주식용 알고리즘 백테스트·기준치 선별 (주식 데이터 기준) — 하니스 지원 완료(#122: `datasource`/`run.py --ch-table`·`metrics.total_tax`+리포트 매도세; #124: `walkforward --ch-table stock_candles_1d`+OOS 매도세 집계). 기준치 선별·유니버스 확정은 잔여(ClickHouse 주식 일봉 적재 후 실행). *후속: `TREND_BARS_PER_YEAR`/연율화를 주식 거래일 ~252로 분기(현 코인 365 공용).*
- [ ] 주식 모의매매 라이브 + 성과 검증

## 9단계 — 통합 자산배분
- [ ] 전체 자산 100 기준 코인+주식 배분 정책 설계 (자산군 비중·리밸런싱)
- [ ] 통합 대시보드 (코인+주식 합산 평가자산/손익)
- [ ] 통합 운영 회고/문서화
