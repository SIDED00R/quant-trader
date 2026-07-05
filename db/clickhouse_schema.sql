-- 원시 틱은 TTL 180일 — 집계(candles_1m/1d)가 영구본이고 원시분은 인트라데이 연구·재집계용이라
-- 반년 보존이면 충분. 무TTL이면 24/7 수집 VM(30GB)이 디스크풀 → 수집 중단으로 간다.
-- ⚠ TTL은 CREATE에만 있어 신규 설치 전용. 기존(라이브) 테이블은 DEPLOY.md 런북의 1회 ALTER로 적용
--   (init_db가 이 파일을 매 부팅 재실행하므로 ALTER MODIFY TTL을 여기 두면 매번 재물질화됨 — 금지).
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
ORDER BY (symbol, seq)
TTL toDateTime(trade_ts) + INTERVAL 180 DAY;

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
ORDER BY (symbol, seq)
TTL toDateTime(trade_ts) + INTERVAL 180 DAY;

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
    market              LowCardinality(String) DEFAULT 'KR',   -- KR(KRX) | US(FINRA). KR 6자리코드와 US 티커는 충돌 없음
    source              LowCardinality(String) DEFAULT 'KRX',
    ingested_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, date);
-- 기존 설치 대응(신규 컬럼 추가는 CREATE IF NOT EXISTS로 반영 안 됨) — 재실행 멱등.
ALTER TABLE stock_short ADD COLUMN IF NOT EXISTS market LowCardinality(String) DEFAULT 'KR' AFTER short_balance_ratio;

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

-- ── 신규 무료 데이터(주식 퀀트 공용) ──

-- 팩터 일간 수익률(Ken French Data Library). 수익 귀인(내 엣지가 알파냐 팩터노출이냐) + 팩터중립 피처.
-- 롱 포맷(팩터 차원) — region으로 US/글로벌/KR(추후) 확장. batch/data/factor_returns.py 적재. 재실행 멱등.
-- ret = 일간 수익률(소수). 원본 CSV는 %표기 → /100 저장. RF도 동일 축에 factor='rf'로 보관.
CREATE TABLE IF NOT EXISTS factor_returns_daily (
    date        Date32,                                -- Date32(1900~): Ken French 팩터는 1926년부터 → Date(1970~) 범위 밖 방지
    region      LowCardinality(String) DEFAULT 'US',   -- US(KenFrench). 글로벌/KR은 추후 확장
    factor      LowCardinality(String),                -- mkt_rf|smb|hml|rmw|cma|rf|umd
    ret         Float64,                               -- 일간 수익률(소수 = 원본%/100)
    source      LowCardinality(String) DEFAULT 'KenFrench',
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (region, factor, date);

-- 내부자 거래(SEC Form 3/4/5, DERA insider-transactions 분기 데이터셋). 내부자 매수/매도 신호.
-- ISSUERTRADINGSYMBOL로 티커 직접 획득(13F와 달리 OpenFIGI 불필요). filed_date로 PIT 게이팅.
-- batch/data/insider.py 적재. trans_code: P(매수)·S(매도)·A(수여)·M(옵션행사)·F(세금納주식)·G(증여) 등.
CREATE TABLE IF NOT EXISTS insider_transactions (
    symbol             LowCardinality(String),
    trans_date         Date,                      -- 거래일(TRANS_DATE)
    filed_date         Date,                      -- 공시 접수일(PIT 게이팅용)
    accession          String,                    -- 공시 접수번호
    trans_sk           String,                    -- NONDERIV_TRANS_SK (accession 내 트랜잭션 유일키)
    owner_cik          String,
    relationship       LowCardinality(String),    -- director|officer|tenpct|other
    trans_code         LowCardinality(String),    -- P|S|A|M|F|G|...
    acquired_disp      LowCardinality(String),    -- A(취득) | D(처분)
    shares             Float64,                   -- 거래 주식수
    price              Float64,                   -- 주당 거래가($)
    shares_owned_after Float64,                   -- 거래 후 보유주식수
    source             LowCardinality(String) DEFAULT 'SEC',
    ingested_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (symbol, trans_date, accession, trans_sk);

-- 실적 발표일 캘린더. US=SEC 8-K Item 2.02(실적) 접수일, KR=DART 실적공시 rcept_dt.
-- 용도: 실적 전후 매매 마스킹(갭 리스크 회피) + 실적발표후표류(PEAD) 신호. batch/data/earnings.py 적재.
CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol        LowCardinality(String),
    market        LowCardinality(String),          -- US | KR
    announce_date Date,                             -- 실적 발표일(8-K 접수일 / DART rcept_dt)
    period_end    Nullable(Date),                   -- 대상 회계기간말(가용 시)
    form          LowCardinality(String),          -- 8-K(2.02) | DART 보고서명
    source        LowCardinality(String) DEFAULT 'SEC',
    ingested_at   DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (market, symbol, announce_date);
