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
