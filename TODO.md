# TODO — 매매 알고리즘 고도화 (앙상블) + 주식(키움) 확장

> 순차 진행. 한 항목 끝나면 체크하고 다음으로. 세션 종료 시 "TODO.md 업데이트해줘"로 진행상황 반영.
> 원칙: 각 단계는 백테스트/회귀로 **검증 가능한 성공 기준**을 갖는다.

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

## 2단계 — 후보 알고리즘 분석 & 구현
- [ ] 대중적 투자 알고리즘 조사 정리 (RSI / MACD / 볼린저밴드 / 돌파·모멘텀 / 평균회귀 등) → `docs/algorithms.md`
- [ ] RSI 전략 구현 (`strategy/rsi.py`)
- [ ] MACD 전략 구현 (`strategy/macd.py`)
- [ ] 볼린저밴드 전략 구현 (`strategy/bollinger.py`)
- [ ] 돌파/모멘텀 전략 구현 (`strategy/breakout.py`)
- [ ] (선택) 평균회귀 등 추가 전략

## 3단계 — 검증 & 부하 선별
- [ ] 전 알고리즘 백테스트 일괄 실행 (동일 기간·심볼·자금)
- [ ] **기준치 정의** (예: Sharpe ≥ X, 승률 ≥ Y, MDD ≤ Z) → `docs/algorithms.md`에 근거 기록
- [ ] 기준 통과 알고리즘을 부하 1·2·3… 로 확정 + 초기 가중치 산정(성과 기반)

## 4단계 — 지휘관(Commander) 앙상블
- [ ] 신호 스키마/토픽 설계: 신규 토픽 `strategy.signals` (`common/schemas.py`에 Signal 추가)
- [ ] 각 부하를 독립 워커로 — `market.ticks` 소비 → `strategy.signals` 발행 (전략명/신호/confidence)
- [ ] `strategy/commander.py`: `strategy.signals` 소비 → 심볼별 윈도우 집계 → 가중 합의 → 최종 BUY/SELL/HOLD
- [ ] Commander 결정 → 기존 `place_order` 경로로 주문 (사이징/청산 규율 적용)
- [ ] 앙상블 백테스트: 단일 SMA baseline 대비 지표 개선 확인 (성공기준: 핵심지표 ≥ baseline)

## 5단계 — 병렬화 & 오케스트레이션
- [ ] 각 부하 워커 + commander를 `docker-compose.yml`에 서비스로 추가 (컨슈머 그룹 분리)
- [ ] 부하 N개 병렬 실행 확인 (Kafka 파티션/컨슈머 그룹)
- [ ] Airflow 도입 — 백테스트/야간 재평가 **배치 DAG** (라이브 워커는 제외)
- [ ] 주기적 재평가 → 가중치 자동 갱신 + 기준치 미달 부하 강등/교체 루프

## 6단계 — 코인 마무리 & 성과 검증
- [ ] 라이브 모의매매로 앙상블 vs 단일 SMA 성과 비교 (일정 기간)
- [ ] 대시보드 성과 패널 추가 (수익률/승률/MDD) — `api/web` + `api/routes`
- [ ] 코인 단계 회고/문서화 (`DESIGN.md`/`BACKLOG.md` 갱신)

## 7단계 — 주식(키움) 토대
- [ ] 키움 API 조사: REST/WebSocket API + 모의투자 계정 발급·인증 흐름 → `docs/kiwoom.md`
- [ ] `stock_ingester`: 키움 실시간 시세 → 신규 토픽 `stock.ticks` (코인 ingester 패턴 재사용)
- [ ] 종목 유니버스 선정 논의/결정 (전 종목 X — 어떤 종목 대상으로 할지) → `docs/kiwoom.md`
- [ ] 주식 체결/계좌 모델: 정수 주문단위·장 시간·수수료/세금 반영 (engine/portfolio 확장 또는 분기)
- [ ] 키움 모의계정으로 단건 주문 왕복 검증 (API → 모의체결 → 내 대시보드 반영)

## 8단계 — 주식 앙상블 (구조 재사용)
- [ ] 코인 `Strategy`/`Commander`/신호버스 구조를 주식에 재사용 (`stock.signals` 토픽)
- [ ] 주식용 알고리즘 백테스트·기준치 선별 (주식 데이터 기준)
- [ ] 주식 모의매매 라이브 + 성과 검증

## 9단계 — 통합 자산배분
- [ ] 전체 자산 100 기준 코인+주식 배분 정책 설계 (자산군 비중·리밸런싱)
- [ ] 통합 대시보드 (코인+주식 합산 평가자산/손익)
- [ ] 통합 운영 회고/문서화
