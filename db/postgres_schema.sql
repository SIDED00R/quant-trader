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

INSERT INTO accounts (account_id, krw_balance)
VALUES ('demo', 10000000)
ON CONFLICT (account_id) DO NOTHING;
