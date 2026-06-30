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

-- 지수 PIT 멤버십(종목별 편입~편출 구간)·편입편출 이벤트. 생존편향 부분해결 + 이벤트 신호.
-- S&P500은 PIT 정확(GitHub fja05680). batch/data/us_membership.py 적재. 재실행 멱등.
CREATE TABLE IF NOT EXISTS index_membership (
    symbol      LowCardinality(String),
    index_name  LowCardinality(String),   -- SP500 | NASDAQ100 | KOSPI200 | KOSDAQ150
    start_date  Date,
    end_date    Date,                      -- 2099-12-31 = 현재 멤버
    source      LowCardinality(String) DEFAULT 'github',
    ingested_at DateTime64(3,'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (index_name, symbol, start_date);

CREATE TABLE IF NOT EXISTS index_changes (
    date        Date,
    symbol      LowCardinality(String),
    index_name  LowCardinality(String),
    action      Enum8('add'=1, 'drop'=2),
    source      LowCardinality(String) DEFAULT 'github',
    ingested_at DateTime64(3,'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (index_name, date, symbol);

-- US 13F 기관보유(SEC DERA, 분기). 우리 512종목만(CUSIP 필터). 보유기관수·총주식수가 robust 신호.
-- VALUE는 2023Q1부터 천$→$ 단위변경 → 보조. batch/data/sec_13f.py 적재. 재실행 멱등.
CREATE TABLE IF NOT EXISTS institutional_13f (
    symbol       LowCardinality(String),
    period_end   Date,
    num_holders  UInt32,
    total_shares Float64,
    total_value  Float64,
    source       LowCardinality(String) DEFAULT 'SEC-DERA',
    ingested_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, period_end);

-- US 섹터(SEC SIC). 산업모멘텀(indmom)·sector-neutral 피처용. batch/data/sec_sector.py 적재.
CREATE TABLE IF NOT EXISTS stock_meta (
    symbol      LowCardinality(String),
    sic         String,
    sic_desc    String,
    sector2     String,                     -- SIC 2자리 major group
    source      LowCardinality(String) DEFAULT 'SEC',
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY symbol;

-- ── KR 외부데이터(KRX 정보데이터시스템, pykrx 로그인). 전부 KR 전용 변수(US 구조적 부재). ──
-- 모두 batch/data/krx.py가 종목별 1패스로 적재(재실행 멱등). 발표 지연 주의 — 사용 시 시프트.

-- KR 투자자별 순매수(12분류). 롱 포맷(투자자 차원) — 외국인/기관/개인 등이 핵심 수급 신호.
-- value=순매수금액(원), volume=순매수수량(주). EOD 장마감(~18시) 확정 → t일은 t종가 이후 게이팅.
CREATE TABLE IF NOT EXISTS stock_investor_flow (
    date        Date,
    symbol      LowCardinality(String),
    investor    LowCardinality(String),   -- foreign|individual|pension|invest_trust|insurance|fin_invest|...
    net_value   Float64,                  -- 순매수금액(원)
    net_volume  Float64,                  -- 순매수수량(주)
    source      LowCardinality(String) DEFAULT 'KRX',
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, date, investor);

-- KR 외국인 보유/한도소진율. 피처는 지분율·한도소진률의 Δ(수준은 다중공선성).
CREATE TABLE IF NOT EXISTS stock_foreign_holding (
    date            Date,
    symbol          LowCardinality(String),
    listed_shares   Float64,              -- 상장주식수
    held_shares     Float64,              -- 외국인 보유수량
    holding_ratio   Float64,              -- 지분율(%)
    limit_shares    Float64,              -- 한도수량
    exhaustion_rate Float64,              -- 한도소진률(%)
    source          LowCardinality(String) DEFAULT 'KRX',
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, date);

-- KR 공매도(잔고+거래량 병합). 잔고 2016-06~, 거래량 ~2017~. T+2 지연 발표 → 사용 시 시프트.
-- 공매도 금지구간(2020-03~2021-05, 2023-11~2025-03)은 0/결측 → 레짐 더미 권장.
CREATE TABLE IF NOT EXISTS stock_short (
    date                Date,
    symbol              LowCardinality(String),
    short_volume        Float64,          -- 공매도 거래량(주)
    total_volume        Float64,          -- 전체 거래량(주)
    short_volume_ratio  Float64,          -- 공매도 비중(%)
    short_balance_qty   Float64,          -- 공매도 잔고수량(주)
    short_balance_value Float64,          -- 공매도 잔고금액(원)
    market_cap          Float64,          -- 시가총액(원)
    short_balance_ratio Float64,          -- 잔고비중(%)
    source              LowCardinality(String) DEFAULT 'KRX',
    ingested_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, date);

-- KR 상장폐지 종목 메타(FDR KRX-DELISTING). 생존편향 보정 — PIT 유니버스(상장~폐지 구간) 구성용.
-- 상폐 종목 OHLCV는 stock_candles_1d(market='KR')에 함께 적재, 폐지일 이후 제외 게이팅은 본 표로.
CREATE TABLE IF NOT EXISTS stock_delisting (
    symbol         LowCardinality(String),
    name           String,
    market         LowCardinality(String),   -- KOSPI | KOSDAQ | KONEX
    listing_date   Date32,                    -- Date32(1900~): 1970년 이전 상장 옛 기업 대응
    delisting_date Date32,
    reason         String,                    -- 폐지사유(감사의견거절·자본잠식·합병 등)
    source         LowCardinality(String) DEFAULT 'FDR',
    ingested_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY symbol;
