# 인트라데이(분봉) 검증 베이스라인 (기능 C)

> 인트라데이 후보 전략을 **현실 비용 차감 후 walk-forward + Deflated Sharpe**로 검증하고 채택 여부를 가른다.
> 코인 일봉 검증(`docs/baseline.md`)과 동일 규율. **실데이터(ClickHouse `stock_candles_1m`) 필요** — 결과는 백필 후 기입.

## 1. 측정 조건 (준비)
- **데이터:** `stock_candles_1m`(토스 분봉, KR=KST/US=ET). 정규장만(백테스트가 시간외 봉 자동 필터, `--all-hours`로 해제).
- **유니버스:** subset-first(고유동성 KR 대형주 + US 대형주). 전 유니버스(KOSPI200+KOSDAQ150+S&P500+NASDAQ-100)는 통과 후 확장.
- **비용:** KR 왕복 ≈0.30%(수수료 0.10% + 매도세 0.20%, 엔진 자동) / US 매도세 0. 슬리피지 스윕 `--slippage-bps 0/5/10/20`(보수).
- **연율화:** 자산군 인지(`periods_per_year`, 주식 분봉 252×390=98,280/년). **생존편향:** 현 구성종목 백필이라 alpha 과대 가능(point-in-time 유니버스는 후속).

## 2. 사전 백필
```bash
docker compose up -d clickhouse && python -m scripts.init_db
python -m batch.backtest.backfill_stock_intraday --symbols 005930,000660,035720,005380,AAPL,MSFT,NVDA --days 365
```
(~5/s rate limit: 50종목 1년 ≈ ~2시간. 종목·기간은 subset부터.)

## 3. 후보별 검증 실행 (walk-forward + DSR)
```bash
# 횡단면(롱 다리) — 대규모 유니버스 본진
python -m batch.backtest.walkforward --source clickhouse --ch-table stock_candles_1m --symbols 005930,000660,035720,005380 --strategy xs_reversal
python -m batch.backtest.walkforward --source clickhouse --ch-table stock_candles_1m --symbols ... --strategy xs_momentum
# 세션 인트라데이(단일종목)
python -m batch.backtest.walkforward --source clickhouse --ch-table stock_candles_1m --symbols ... --strategy orb
python -m batch.backtest.walkforward --source clickhouse --ch-table stock_candles_1m --symbols ... --strategy intraday_momentum
# 참고: 기존 전략도 분봉에 적용 가능(breakout 등)
```
- 단발 백테스트(요약·CSV): `python -m batch.backtest.run --source clickhouse --ch-table stock_candles_1m --symbols ... --strategy xs_reversal --sample-sec 60 --out runs/xs_reversal`
- 빈도 탐색(중빈도): 분봉을 5/15/30분으로 다운샘플해 비교(현재 walkforward clickhouse는 원봉 1분 — 리샘플은 후속).

## 4. 채택 게이트 (모두 충족)
- 현실 비용 차감 후 **OOS 합성수익 양(+)**
- **Deflated/Probabilistic Sharpe ≥ 0.90** (고정구성 N=1 → PSR; 다전략 비교 시 시도수 정직 반영)
- 양수 OOS fold 비율 충분(코인 채택 앙상블 65% 참고)
- 비용/자본 비율 상한 이내(`oos_total_fees`+`oos_total_tax`)
- 슬리피지 스윕에서 엣지 생존

## 5. 결과 (실데이터 후 기입 — TBD)

| 전략 | 빈도 | 유니버스 | OOS 합성 | 양수 fold | OOS Sharpe | DSR/PSR | 비용/자본 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| xs_reversal | 1분 | KR subset | — | — | — | — | — | — |
| xs_momentum | 1분 | KR subset | — | — | — | — | — | — |
| orb | 1분 | KR subset | — | — | — | — | — | — |
| intraday_momentum | 1분 | KR subset | — | — | — | — | — | — |
| (US subset, 중빈도 …) | | | | | | | | |

## 6. 정직한 예상 (연구 근거)
research(`docs/intraday_research.md`)상 **단일종목 순수 1분(orb/intraday_momentum)은 비용에 약함**(과매매), **횡단면 롱 다리·중빈도**가 통과 여지 큼. 전부 게이트 미달이면 **"고빈도 기각, 일봉 앙상블 유지"가 검증된 결론**(정당한 음성 결과). 통과 전략만 단계 4(라이브) 후보.
