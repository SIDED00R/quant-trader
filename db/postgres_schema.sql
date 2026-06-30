CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    krw_balance  NUMERIC(20,4) NOT NULL CHECK (krw_balance >= 0),
    auto_trade   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 기존 DB 에도 자동매매 토글 컬럼 보장(멱등)
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS auto_trade BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS positions (
    account_id     TEXT NOT NULL REFERENCES accounts(account_id),
    symbol         TEXT NOT NULL,
    quantity       NUMERIC(28,12) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    avg_buy_price  NUMERIC(20,4) NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id     UUID PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    type         TEXT NOT NULL,
    price        NUMERIC(20,4),
    quantity     NUMERIC(28,12) NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id  UUID PRIMARY KEY,
    order_id      UUID NOT NULL REFERENCES orders(order_id),
    account_id    TEXT NOT NULL REFERENCES accounts(account_id),
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    price         NUMERIC(20,4) NOT NULL,
    quantity      NUMERIC(28,12) NOT NULL,
    fee           NUMERIC(20,4) NOT NULL DEFAULT 0,
    executed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 전략 부하 가중치(5단계): 재평가 잡이 갱신, commander가 읽어 신호 합성. 없으면 commander는 동일가중.
CREATE TABLE IF NOT EXISTS strategy_weights (
    strategy    TEXT PRIMARY KEY,
    weight      NUMERIC(10,6) NOT NULL DEFAULT 1 CHECK (weight >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 주문 발행 아웃박스: 주문 INSERT와 같은 트랜잭션에 기록 → orders 토픽 발행을 원자적으로 보장
CREATE TABLE IF NOT EXISTS order_outbox (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL,
    symbol      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    published   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON order_outbox (id) WHERE NOT published;

-- 매매 결정 기록: trade_once가 매 실행마다 종목별로 1행(매매·유지 전부) 남긴다. 대시보드 '매매 결정 기록' 탭이 읽음.
CREATE TABLE IF NOT EXISTS trade_decisions (
    decision_id   UUID PRIMARY KEY,
    run_ts        TIMESTAMPTZ NOT NULL DEFAULT now(),   -- trade_once 실행 시각(한 실행=동일값)
    bar_date      DATE,                                 -- 분석 기준 일봉 날짜(candles_1d 최신 완료봉)
    account_id    TEXT NOT NULL REFERENCES accounts(account_id),
    symbol        TEXT NOT NULL,
    price         NUMERIC(20,4),                        -- 분석 시점 시세
    target_weight NUMERIC(10,6),                        -- 합성 목표비중(0~1, NULL=신호 불완전)
    action        TEXT NOT NULL,                        -- 'BUY' | 'SELL' | 'HOLD'
    quantity      NUMERIC(28,12),                       -- 매매 시 수량(HOLD면 NULL)
    amount_krw    NUMERIC(20,4),                        -- 예상/체결 금액 = quantity*price
    equity        NUMERIC(20,4),                        -- 결정 시점 평가자산
    reason        TEXT NOT NULL,                        -- 사람이 읽는 사유(한국어)
    signals       JSONB,                                -- 부하별 근거 [{load,target,sma_s,sma_l,ann_vol,state}]
    executed      BOOLEAN NOT NULL DEFAULT FALSE        -- 실제 체결 여부(rejected=false)
);

CREATE INDEX IF NOT EXISTS idx_decisions_run ON trade_decisions (account_id, run_ts DESC);

-- 주간 리밸런싱 멱등 마커: 평일 스케줄(휴장·실패 재시도)에서 한 주 1회만 실제 매매하도록 보장.
-- 그 주 완료 시 1행 기록 → 같은 주 후속 평일 부팅은 skip. (market, iso_week)로 중복 차단.
CREATE TABLE IF NOT EXISTS weekly_rebalance (
    market    TEXT NOT NULL,                          -- 'US' | 'KR'
    iso_week  TEXT NOT NULL,                           -- ISO 주차 'YYYY-Www'(거래소 로컬 날짜 기준)
    done_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market, iso_week)
);

INSERT INTO accounts (account_id, krw_balance)
VALUES ('demo', 10000000)
ON CONFLICT (account_id) DO NOTHING;
