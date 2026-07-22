-- 코인 과거분 백필(멱등): trade_decisions의 결정 시점 equity를 일 단위로 시딩. init_db가 마이그레이션 후
-- 매 부팅 재실행하므로, 라이브 스냅샷이 실패한 날도 다음 부팅에 결정 기록으로 자가 회복된다. 라이브 행 우선(DO NOTHING).
-- 같은 run_ts 안에서 equity는 종목 순회 중 수수료만큼 드리프트(≤0.05%)·시세 없는 행은 NULL → MAX+NULL 제외.
-- 마이그레이션(0001_baseline)이 아닌 repair로 분리 — 버전 이력에 남기지 않고 매 부팅 재실행되는 자가회복 문장.
INSERT INTO equity_snapshots (ts, snap_date, market, account_id, currency, equity)
SELECT max(run_ts), (run_ts AT TIME ZONE 'UTC')::date, 'COIN', account_id, 'KRW', max(equity)
FROM trade_decisions WHERE equity IS NOT NULL
GROUP BY account_id, (run_ts AT TIME ZONE 'UTC')::date
ON CONFLICT (market, account_id, snap_date) DO NOTHING;
