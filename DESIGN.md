# 실시간 코인 모의거래 시스템 — 설계 문서

> Kafka를 중심으로 한 실시간 이벤트 파이프라인 학습 프로젝트.
> 업비트 실시간 시세를 받아 가상 자금으로 매수/매도하고, 체결·포트폴리오·손익을 실시간으로 보여준다.

---

## 1. 목표와 범위

### 1.1 목표
- Kafka의 핵심 개념(파티셔닝, consumer group, replay, 스트림 집계, 멱등성)을 실제 동작하는 시스템으로 체득한다.
- OLTP(PostgreSQL)와 OLAP(ClickHouse)의 역할 분리를 경험한다.
- 로컬(Docker Compose)에서 완성한 뒤 GCP로 이전한다.

### 1.2 범위 (MVP)
- 업비트 KRW 마켓 실시간 체결가 수집
- 가상 계좌(초기 자금 지급) 기반 모의거래
- 시장가 주문 → 지정가 주문 순으로 확장
- 실시간 틱/캔들 적재 및 대시보드

### 1.3 비범위 (이번 프로젝트에서 다루지 않음)
- 실제 사용자 인증/보안(OAuth 등) — 학습용 단순 user_id 식별로 대체
- 사용자 간 호가 매칭(order book) — 시장가 기준 모의 체결로 대체
- 레버리지/선물/마진 거래

---

## 2. 핵심 설계 결정 (가정)

| 항목 | 결정 | 근거 |
|------|------|------|
| 거래소 | **업비트** WebSocket (`wss://api.upbit.com/websocket/v1`) | 무료, 인증 불필요, 24시간, 고빈도 틱 |
| 대상 종목 | 설정 가능한 watchlist (기본: `KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE`) | 파티셔닝 효과를 보기 위해 5개로 시작 |
| 사용자 | 다중 사용자 구조이되 데모 계정 1개로 시작(초기 자금 10,000,000 KRW) | 학습 단순화, 인증은 후순위 |
| 주문 유형 | 시장가(MARKET) → 지정가(LIMIT) 순 | 단계적 난이도 |
| 체결 모델 | **모의 체결** — 실시간 최신가 기준. 시장가는 즉시, 지정가는 가격 도달 시 | 호가창 없이 학습 목적 달성 |
| 수수료 | 설정 가능(기본 0.05%) | 현실성, 손익 계산 학습 |
| 정합성 | 잔고 변경은 Postgres 트랜잭션, `order_id`/`execution_id`로 멱등 처리 | 중복 체결 방지 |
| 직렬화 | JSON (1차) → 추후 Avro + Schema Registry(심화) | 빠른 시작 후 확장 |

---

## 3. 아키텍처

```
 업비트 WebSocket
       │  (실시간 체결 틱)
       ▼
┌──────────────┐
│ Market 수집기 │──▶ [market.ticks]  (key=symbol)
└──────────────┘        │
                        ├──▶ [틱 Sink]    ──▶ ClickHouse (raw 틱)
                        ├──▶ [캔들 집계기] ──▶ ClickHouse (1분봉 candles_1m)
                        │
사용자 ─▶ FastAPI ─▶ [orders] (key=symbol)
                        │
                        ▼
                 ┌──────────────┐
                 │   체결 엔진    │  ← ticks + orders 동시 소비
                 │ (종목별 최신가 │     · 시장가: 즉시 체결
                 │  추적 + 매칭)  │     · 지정가: 가격 도달까지 보관
                 └──────────────┘
                        │
                        ▼
                  [executions] (key=symbol)
                     ├──▶ 포트폴리오 서비스 ──▶ PostgreSQL (잔고/포지션)
                     └──▶ 알림 서비스(선택) — 목표가 도달 등
```

### 3.1 핵심 포인트
- **`market.ticks`와 `orders`를 모두 종목(symbol)으로 파티셔닝**한다. 그러면 체결 엔진의 한 인스턴스가 같은 종목의 틱과 주문을 같은 파티션에서 순서대로 소비하여 매칭할 수 있다. → 파티셔닝·consumer group을 자연스럽게 학습.
- 같은 `market.ticks`를 **여러 consumer group**(틱 Sink / 캔들 집계기 / 체결 엔진)이 각자 독립적으로 소비한다. → consumer group의 핵심 가치 체득.
- 보관 기간(retention) 동안 틱 로그가 남으므로, offset을 되감아 **하루치 재처리(backtest)**가 가능하다. → replay 학습.

---

## 4. 기술 스택

| 영역 | 선택 |
|------|------|
| 언어 | Python (`confluent-kafka`, FastAPI, `clickhouse-connect`, `psycopg`) |
| 메시지 | Apache Kafka (KRaft 모드, Zookeeper 없음) |
| OLTP | PostgreSQL |
| OLAP | ClickHouse |
| 대시보드 | Grafana (또는 FastAPI + 간단 프론트) |
| 로컬 오케스트레이션 | Docker Compose |

---

## 5. Kafka 토픽 설계

| 토픽 | 키 | 파티션 | 보관 | 내용 |
|------|-----|--------|------|------|
| `market.ticks` | symbol | 6 | 1일 (시간 기반) | 실시간 체결가 틱 |
| `orders` | symbol | 6 | 7일 | 사용자 매수/매도 주문 |
| `executions` | symbol | 6 | 7일 | 체결 결과(fill) |

### 5.1 이벤트 스키마 (JSON)

**tick** (`market.ticks`)
```json
{
  "symbol": "KRW-BTC",
  "price": 95000000,
  "volume": 0.012,
  "side": "BID",
  "trade_ts": "2026-06-13T12:00:00.123Z",
  "seq": 174981234
}
```

**order** (`orders`)
```json
{
  "order_id": "uuid",
  "account_id": "demo",
  "symbol": "KRW-BTC",
  "side": "BUY",
  "type": "MARKET",
  "price": null,
  "quantity": 0.001,
  "ts": "2026-06-13T12:00:01.000Z"
}
```

**execution** (`executions`)
```json
{
  "execution_id": "uuid",
  "order_id": "uuid",
  "account_id": "demo",
  "symbol": "KRW-BTC",
  "side": "BUY",
  "price": 95000000,
  "quantity": 0.001,
  "fee": 47.5,
  "ts": "2026-06-13T12:00:01.050Z"
}
```

---

## 6. 데이터 모델

### 6.1 PostgreSQL (OLTP — 정합성 중요)

```sql
-- 가상 계좌
accounts(
  account_id      TEXT PRIMARY KEY,
  krw_balance     NUMERIC(20,4) NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
)

-- 보유 포지션
positions(
  account_id      TEXT REFERENCES accounts,
  symbol          TEXT,
  quantity        NUMERIC(28,12) NOT NULL DEFAULT 0,
  avg_buy_price   NUMERIC(20,4)  NOT NULL DEFAULT 0,
  PRIMARY KEY (account_id, symbol)
)

-- 주문
orders(
  order_id        UUID PRIMARY KEY,
  account_id      TEXT REFERENCES accounts,
  symbol          TEXT,
  side            TEXT,          -- BUY | SELL
  type            TEXT,          -- MARKET | LIMIT
  price           NUMERIC(20,4), -- LIMIT일 때만
  quantity        NUMERIC(28,12),
  status          TEXT,          -- PENDING | FILLED | CANCELLED
  created_at      TIMESTAMPTZ DEFAULT now()
)

-- 체결 내역 (멱등 키)
executions(
  execution_id    UUID PRIMARY KEY,
  order_id        UUID REFERENCES orders,
  account_id      TEXT,
  symbol          TEXT,
  side            TEXT,
  price           NUMERIC(20,4),
  quantity        NUMERIC(28,12),
  fee             NUMERIC(20,4),
  executed_at     TIMESTAMPTZ DEFAULT now()
)
```

### 6.2 ClickHouse (OLAP — 대용량 시계열)

```sql
-- raw 틱
ticks(
  symbol      LowCardinality(String),
  price       Float64,
  volume      Float64,
  side        LowCardinality(String),
  trade_ts    DateTime64(3),
  seq         UInt64,
  ingest_ts   DateTime64(3)
) ENGINE = ReplacingMergeTree(ingest_ts) ORDER BY (symbol, seq)

-- 1분봉 (집계기가 적재, 또는 Materialized View)
candles_1m(
  symbol        LowCardinality(String),
  window_start  DateTime,
  open Float64, high Float64, low Float64, close Float64,
  volume Float64,
  updated_at    DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(updated_at) ORDER BY (symbol, window_start)
```

---

## 7. 컴포넌트 (파일 단일 책임 원칙)

> 폴더는 **실행 단계**를 드러낸다: `streaming/`(수집→집계) → `trading/`(신호→체결). `batch/`·`api/`·`common/`은 직교(파이프라인 단계 아님).

```
kafka_project/
├── docker-compose.yml          # Kafka(KRaft) + Postgres + ClickHouse + Grafana
├── Dockerfile / Dockerfile.batch
├── DESIGN.md / README.md / .env.example / requirements.txt
│
├── common/                     # 공용 라이브러리 (파이프라인 단계 아님)
│   ├── config.py · constants.py            # 설정 / 고정 상수(컬럼리스트·HTTP·KIS TR)
│   ├── kafka_client.py · clickhouse_client.py · postgres_client.py
│   ├── http_client.py · oauth_token.py     # 공용 HTTP 재시도 / 토큰 캐시
│   ├── schemas.py · candles.py · rate_limit.py
│   └── kiwoom_client · toss_client · kis_client · kis_account
│
├── streaming/                  # 연속 데이터: 수집 → 적재 → 집계
│   ├── ingester/   upbit_ws · stock_kiwoom          # 시세 → Kafka market/stock.ticks
│   ├── sink/       tick_clickhouse · stock_tick_clickhouse   # ticks → ClickHouse
│   └── aggregator/ candle(1m) · daily(1d)
│
├── trading/                    # 신호 → 체결
│   ├── strategy/   ensemble · trend · live_ensemble · commander · trade_once …
│   ├── engine/     matching                          # orders+ticks → executions
│   ├── portfolio/  updater                           # executions → Postgres 잔고/포지션
│   └── relay/      order_relay                        # outbox → orders 토픽
│
├── batch/                      # 오프라인/배치 (프로덕션 이미지 제외)
│   └── backtest/   run · walkforward · metrics · upbit_daily · toss_daily …
│
├── api/        # FastAPI 대시보드/REST (서빙 — 직교)
├── scripts/    # init_db 등 1회성 셋업
├── db/         # 스키마 DDL (postgres_schema.sql · clickhouse_schema.sql)
└── dashboard/  # Grafana 정의(프로비저닝)
```

### 실행 순서 · 의존 맵 (A 완료 → B 가능)

- `scripts.init_db` → 모든 서비스 (스키마 생성 선행)
- `streaming.ingester` → `streaming.sink` · `streaming.aggregator` (market.ticks 흐름)
- `streaming.aggregator.candle` → `.daily` (candles_1m → candles_1d)
- `streaming.aggregator.daily` → `trading.strategy.live_ensemble` (candles_1d 워밍업)
- `trading.strategy.live_ensemble` → `.commander` (strategy.signals)
- `.commander` → `trading.relay` → `trading.engine` → `trading.portfolio` (orders → executions → 잔고)
- candles_1d 완성 → `trading.strategy.trade_once` (일 1회 온디맨드 배치)
- candles_1d(전기간) → `batch.backtest.reeval_weights` (가중치 재평가)

---

## 8. 단계별 구현 계획 (각 단계 = 기능 1개 = 1 PR)

각 단계는 **개발 → 검증 → commit → PR → 코드리뷰 → 동작확인 → (승인 시) merge** 사이클을 따른다.

| # | 기능 | 검증 기준 |
|---|------|-----------|
| 0 | 로컬 인프라 (docker-compose: Kafka+Postgres+ClickHouse) | 컨테이너 기동, 토픽 생성, console produce/consume 성공 |
| 1 | Market 수집기 (업비트 WS → `market.ticks`) | `kafka-console-consumer`로 틱이 흐르는지 확인 |
| 2 | 틱 Sink → ClickHouse | `SELECT count() FROM ticks` 가 증가 |
| 3 | 주문 API + Postgres 스키마 | 주문 POST → `orders` 토픽에 메시지 + DB에 PENDING 기록 |
| 4 | 체결 엔진 (시장가) | 주문 → `executions`에 fill 생성 |
| 5 | 포트폴리오 서비스 | 체결 후 Postgres 잔고/포지션 변동 확인 |
| 6 | 캔들 집계기 → ClickHouse | 1분봉 row 생성 확인 |
| 7 | 지정가 주문 (가격 도달까지 보관) | 목표가 도달 시 체결 확인 |
| 8 | Grafana 대시보드 (시세/손익/체결량) | 대시보드에 실시간 데이터 표시 |
| 9 | GCP 배포 | 클라우드에서 동일 파이프라인 동작 |

---

## 9. GCP 배포 매핑

| 컴포넌트 | GCP 서비스 |
|----------|-----------|
| Kafka | Managed Service for Apache Kafka (또는 Confluent Cloud) |
| PostgreSQL | Cloud SQL for PostgreSQL |
| ClickHouse | ClickHouse Cloud 또는 GCE/GKE self-host |
| 수집기·소비자(상시 구동) | GKE (장기 실행 consumer에 적합) |
| 주문 API | Cloud Run 또는 GKE |
| 대시보드 | GCE의 Grafana 또는 Grafana Cloud |

> Kafka consumer는 상시 떠 있어야 하므로 Cloud Run보다 GKE가 적합. 로컬에서 0~8단계를 완성한 뒤 9단계에서 GCP로 이전한다.

---

## 10. 학습 체크포인트 (Kafka 개념 ↔ 구현 매핑)

| Kafka 개념 | 어디서 학습되나 |
|------------|-----------------|
| 파티셔닝(키 기반 순서) | `market.ticks`/`orders`를 symbol로 분산 |
| Consumer Group | 같은 틱을 Sink/집계기/체결엔진이 독립 소비 |
| Replay(offset 되감기) | 하루치 틱 재처리 백테스트 |
| 스트림 윈도우 집계 | 캔들 집계기(1분봉) |
| 멱등성/중복 방지 | `execution_id` 기반 잔고 반영 |
| 보관/압축 정책 | 토픽별 retention 설정 |
```
