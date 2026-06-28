CREATE TABLE IF NOT EXISTS ticks (
    symbol     LowCardinality(String),
    price      Float64,
    volume     Float64,
    side       LowCardinality(String),
    trade_ts   DateTime64(3, 'UTC'),
    seq        UInt64,
    ingest_ts  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingest_ts)
ORDER BY (symbol, seq);

CREATE TABLE IF NOT EXISTS stock_ticks (
    symbol     LowCardinality(String),
    price      Float64,
    volume     Float64,
    side       LowCardinality(String),
    trade_ts   DateTime64(3, 'UTC'),
    seq        UInt64,
    ingest_ts  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingest_ts)
ORDER BY (symbol, seq);

CREATE TABLE IF NOT EXISTS candles_1m (
    symbol        LowCardinality(String),
    window_start  DateTime('UTC'),
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    updated_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (symbol, window_start);

CREATE TABLE IF NOT EXISTS candles_1d (
    symbol        LowCardinality(String),
    window_start  DateTime('UTC'),
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    updated_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (symbol, window_start);

-- 주식 일봉(토스증권 백필). 코인 candles_1d와 분리 — KR/US 혼재라 통화/시장 차원이 필요.
-- window_start = 캔들의 현지(KST 표기) 날짜 00:00 UTC로 정규화(하루 1행). ReplacingMergeTree로 재실행 멱등.
CREATE TABLE IF NOT EXISTS stock_candles_1d (
    symbol        LowCardinality(String),
    window_start  DateTime('UTC'),
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    currency      LowCardinality(String),   -- KRW | USD
    market        LowCardinality(String),   -- KR | US
    updated_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (symbol, window_start);

-- 주식 분봉(인트라데이 연구·검증, 플랜 단계 0). stock_candles_1d와 동일 구조(통화/시장 차원 포함).
-- window_start = 분봉 시작 시각(UTC). 백필(토스/키움/US) 또는 stock.ticks 라이브 집계로 적재. 재실행 멱등.
CREATE TABLE IF NOT EXISTS stock_candles_1m (
    symbol        LowCardinality(String),
    window_start  DateTime('UTC'),
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    currency      LowCardinality(String),   -- KRW | USD
    market        LowCardinality(String),   -- KR | US
    updated_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (symbol, window_start);

-- ML 피처 저장(단계: ML 피처 파이프라인). 롱 포맷(피처 추가가 잦아 스키마 변경 없이 확장).
-- value = raw 피처값(횡단면 rank/z 정규화는 학습 시점에 적용). batch/features/compute.py가 적재.
-- 재실행 멱등(ReplacingMergeTree). 학습 시 (symbol,date) 피벗해 wide로 사용.
CREATE TABLE IF NOT EXISTS stock_features_daily (
    symbol      LowCardinality(String),
    date        Date,
    market      LowCardinality(String),   -- KR | US
    feature     LowCardinality(String),
    value       Float64,
    updated_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY market
ORDER BY (symbol, date, feature);

-- US 펀더멘털 원본(SEC EDGAR companyfacts). 롱 포맷·vintage 보존(정정공시 대비 filed_date별 보관).
-- point-in-time: 사용 시 filed_date ≤ 거래일 게이팅. batch/data/fundamentals.py가 적재(재실행 멱등).
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    symbol       LowCardinality(String),
    concept      LowCardinality(String),   -- shares|equity|assets|net_income|revenue|op_cashflow
    period_end   Date,
    filed_date   Date,
    form         LowCardinality(String),   -- 10-Q | 10-K | ...
    duration_d   UInt16,                   -- flow 기간(일). instant=0
    value        Float64,
    source       LowCardinality(String) DEFAULT 'EDGAR',
    ingested_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, concept, period_end, filed_date);

-- 매크로 시계열(FRED). 전 종목 공통 레짐 피처(금리·수익률곡선·VIX·환율·유가). 휴일 전일캐리(ffill).
-- batch/data/fred.py가 적재(FRED_API_KEY 필요). 일별 증분: 재실행 시 최신 추가(멱등).
CREATE TABLE IF NOT EXISTS macro_daily (
    date     Date,
    dgs10    Float64,  dgs2  Float64,  dgs3mo Float64,
    t10y2y   Float64,  t10y3m Float64,
    vix      Float64,  usdkrw Float64, dxy Float64, wti Float64,
    source   LowCardinality(String) DEFAULT 'FRED',
    ingested_at DateTime64(3,'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY date;
