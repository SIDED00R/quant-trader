CREATE TABLE IF NOT EXISTS ticks (
    symbol     LowCardinality(String),
    price      Float64,
    volume     Float64,
    side       LowCardinality(String),
    trade_ts   DateTime64(3, 'UTC'),
    ingest_ts  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (symbol, trade_ts);
