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
