# coin-auto-trader

실시간 코인 모의거래 시스템 — Kafka 기반 실시간 이벤트 파이프라인 학습 프로젝트.

업비트 실시간 시세를 수집·저장하고, **일봉 추세추종 앙상블 전략**으로 가상 자금을 자동 매매하며, 체결·포트폴리오·손익을 대시보드로 보여준다. 데이터 수집은 상시, 매매는 일봉 1회(온디맨드)로 분리한 구조다.

## 아키텍처 개요

```
[수집·저장 (상시)]
업비트 WS → ingester → [Kafka market.ticks] ─┬→ sink → ClickHouse(틱)
                                             └→ candle → 1분봉 → daily-aggregator → 일봉(candles_1d)
[자동매매]
live_ensemble (일봉 마감마다 부하별 목표비중 신호)
trade_once (일봉 합성 목표비중 → 주문·체결 동기 처리) → PostgreSQL(계좌/주문/포지션)
[조회] FastAPI 대시보드 · Grafana
```

- **메시지**: Apache Kafka (KRaft) — 틱 한 스트림을 적재·집계 소비자에게 **팬아웃**
- **OLTP**: PostgreSQL (잔고/주문/포지션/전략가중치, outbox 패턴)
- **OLAP**: ClickHouse (틱/캔들/분석)
- **API/대시보드**: FastAPI + Grafana (+ Caddy 자동 HTTPS)
- **전략**: 일봉 저회전 추세추종 앙상블(5/40·10/60·20/100), walk-forward·Deflated Sharpe 검증 (`docs/model.md`)
- **프로덕션 배포**: **2-VM** — 상시 데이터 VM + Cloud Scheduler가 매일 01:00 UTC 켜는 온디맨드 매매 VM([DEPLOY.md](DEPLOY.md) §11)

> 참고: 위 자동매매 외에, 수동 주문 API(`POST /orders`)와 실시간 스트리밍 체결 파이프라인(`commander`/`engine`/`portfolio`)도 코드로 존재한다(로컬 개발·디버깅용). 프로덕션 라이브 매매 경로는 `trade_once`(동기 배치)다.

전체 설계/흐름은 [DESIGN.md](DESIGN.md) · [project_flow.md](project_flow.md) 참고.

## 로컬 실행 (인프라)

```bash
# 1) 환경변수 준비
cp .env.example .env

# 2) 인프라 기동 (Kafka + PostgreSQL + ClickHouse + 토픽 자동 생성)
docker compose up -d

# 2-1) DB 스키마 1회 적용 (앱 런타임과 분리)
.venv/Scripts/python -m scripts.init_db

# 3) 검증
docker compose ps                                   # 컨테이너 상태 확인
docker compose logs kafka-init                       # 생성된 토픽 목록 확인
docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

# 4) produce/consume 왕복 테스트
docker exec -i kafka /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 --topic market.ticks   # 입력 후 메시지 타이핑
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic market.ticks --from-beginning --max-messages 1

# 종료
docker compose down            # 컨테이너 제거 (볼륨 유지)
docker compose down -v         # 볼륨까지 삭제
```

접속 정보: 웹 대시보드 `127.0.0.1:8000` · Kafka `127.0.0.1:9092` · PostgreSQL `127.0.0.1:5432` · ClickHouse HTTP `127.0.0.1:8123` · Grafana `127.0.0.1:3000` (admin/admin)
(컨테이너 포트는 보안상 `127.0.0.1` 루프백에만 바인딩. Windows에서 `localhost`가 IPv6 `::1`로 풀리는 문제를 피하려 호스트 접속은 `127.0.0.1`을 사용합니다.)

### 서비스 실행

docker-compose는 프로파일로 묶여 있다(서비스 코드는 동일, 실행 묶음만 다름):

```bash
docker compose --profile app  up -d --build   # 로컬 풀스택(수집+스트리밍 매매+대시보드)
docker compose --profile data up -d --build   # 프로덕션 데이터 VM(수집·저장·대시보드만)
docker compose --profile trade run --rm trade-once python -m trading.strategy.trade_once   # 온디맨드 1회 매매
docker compose --profile batch run --rm reeval                                     # 부하 재평가 배치
```

개별 워커를 디버깅용으로 직접 돌릴 수도 있다(각각 별도 터미널):

```bash
.venv/Scripts/python -m streaming.ingester.upbit_ws      # 업비트 WS → market.ticks
.venv/Scripts/python -m streaming.sink.tick_clickhouse   # market.ticks → ClickHouse
.venv/Scripts/python -m trading.engine.matching        # 체결 엔진(시장가)
.venv/Scripts/python -m trading.portfolio.updater      # executions → 잔고/포지션
.venv/Scripts/python -m trading.relay.order_relay      # 주문 outbox → orders 토픽
.venv/Scripts/python -m uvicorn api.main:app --port 8000   # 주문 API

# 주문 넣고 잔고 확인
curl -X POST 127.0.0.1:8000/orders -H "Content-Type: application/json" \
  -d '{"symbol":"KRW-BTC","side":"BUY","type":"MARKET","quantity":0.001}'
curl 127.0.0.1:8000/accounts/demo
```

### 웹 대시보드

브라우저에서 `http://127.0.0.1:8000` 접속 → 실시간 시세·잔고·평가손익·주문·체결내역을 한 화면에서 확인(2초 폴링). 모든 시각은 **KST(Asia/Seoul)** 표시(데이터는 UTC 저장).

- 외부 공개(인터넷)는 `.env` 에 `API_BIND=0.0.0.0` + `WEB_PASSWORD=<강한 비번>` 설정 시 Basic Auth로 보호. GCP 배포는 [DEPLOY.md](DEPLOY.md) §11 참고.

## 알려진 한계 (학습용 MVP)

- **체결 엔진은 단일 인스턴스 전제**: 최신가·pending이 인메모리이고 ticks/orders가 별도 토픽이라, 컨슈머 그룹으로 스케일아웃하면 파티션 분배가 어긋나 동작이 깨진다. 재시작 직후 워밍업 구간에는 약간 과거 가격으로 체결될 수 있다(틱 재생 기반).
- **정밀도 분리**: 금액·수량은 Postgres `NUMERIC` + Python `Decimal`로 무손실 처리. ClickHouse `ticks`는 분석용이라 `Float64`.
- **모의 체결**: 사용자 간 호가 매칭 없이 실시간 최신가로 체결한다.

## 진행 상태

- [x] 0. 로컬 인프라 (docker-compose)
- [x] 1. Market 수집기 (업비트 WS → market.ticks)
- [x] 2. 틱 Sink → ClickHouse
- [x] 3. 주문 API + Postgres 스키마
- [x] 4. 체결 엔진 (시장가)
- [x] 5. 포트폴리오 서비스
- [x] 6. 캔들 집계기 → ClickHouse
- [x] 7. 지정가 주문
- [x] 8. Grafana 대시보드
- [x] 9. GCP 배포 — **2-VM**(상시 데이터 + 온디맨드 매매), Cloud Scheduler ([DEPLOY.md](DEPLOY.md) §11)
- [x] 10. 자동매매 전략 — 일봉 추세추종 앙상블 + walk-forward/Deflated Sharpe 검증 + 라이브(모의) 배포

> 전략·인프라 로드맵 상세는 [TODO.md](TODO.md) · [project_flow.md](project_flow.md) 참고.
