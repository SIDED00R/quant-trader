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

## 5. 결과 (1차 실측 — KR 고유동성 6종목: 005930·000660·035720·005380·000270·035420)

데이터: `stock_candles_1m` 6종목 250일(2025-10~2026-06, 정규장 필터 39.3만봉) / `stock_candles_1d` 6종목 5.5년(2020~2026).
비용: 수수료 0.10% 왕복 + 국내 매도세 0.20% (슬리피지 0). 초기 1,000만원. walk-forward(고정구성 → PSR).

### 5.1 1분봉 (매 분 리밸런싱) — 전멸
| 전략 | OOS 합성 | 양수 fold | OOS Sharpe | PSR | 거래수 | 수수료+매도세 |
|---|---|---|---|---|---|---|
| xs_reversal | **−99.98%** | 0/6 | −26.4 | 0.000 | 19,817 | 55.5M (자본 5.5배) |
| xs_momentum | **−100.0%** | 0/6 | −35.7 | 0.000 | 18,505 | 43.6M |
| orb (세션) | −4.30% | 3/6 | −0.25 | 0.348 | 349 | 1.5M |
| intraday_momentum (세션) | −1.60% | 4/6 | −0.07 | 0.459 | 295 | 1.3M |

→ 횡단면을 **매 분 리밸런싱**하면 회전율 폭발(~2만 거래)로 비용이 자본의 4~5배 → 전멸(코인 1분봉 −98%와 동일 구조). 저회전 세션 전략(orb/momentum)은 비용은 관리되나 음수·게이트 미달.

### 5.2 일봉 cadence (1일 1회 리밸런싱) — 게이트 통과 발견
| 전략 | OOS 합성 | 양수 fold | OOS Sharpe | PSR | 거래수 | 게이트 |
|---|---|---|---|---|---|---|
| xs_reversal (lb5) | −7.99% | 5/9 | −0.01 | 0.486 | 738 | ✗ |
| xs_reversal (lb20) | −0.84% | 4/8 | 0.05 | 0.547 | 321 | ✗ |
| xs_momentum (lb20) | +42.6% | 5/8 | 0.50 | 0.884 | 324 | ✗(근접) |
| **xs_momentum (lb60)** | **+116.1%** | **6/8** | **0.98** | **0.991** | 129 | **✓** |

→ **빈도가 결정변수.** 동일 횡단면 모멘텀이 1분봉 −100%(비용사) → 일봉 **+116%·PSR 0.99**. 연구(§2.4) 예측대로 **저회전 횡단면 모멘텀(롱 다리)** 이 본진. 모멘텀>리버설, 긴 룩백(60일)이 회전율↓·성과↑.
(위 5.2는 `top_n=3`+`max_weight=0.2`라 3×20%=60%만 투자·40% idle cash — 6종목이라 top_n을 작게 둔 artifact.)

### 5.3 항상 풀투자(top-N 1/N씩 100% 투자, idle cash 제거)
| config | OOS 합성 | 양수 fold | OOS Sharpe | PSR | 거래수 | 게이트 |
|---|---|---|---|---|---|---|
| **xs_momentum lb60 N3 (33%씩)** | **+215.5%** | 6/8 | 0.96 | 0.990 | 124 | **✓** |
| xs_momentum lb60 N2 (50%씩) | +178.3% | 6/8 | 0.81 | 0.974 | 107 | ✓ |
| xs_momentum lb20 N3 | +63.6% | 5/8 | 0.48 | 0.876 | 322 | ✗(근접) |
| xs_momentum lb120 N3 | +20.7% | 4/8 | 0.26 | 0.731 | 92 | ✗ |
| xs_reversal lb20 N3 | −6.1% | 4/8 | 0.05 | 0.550 | 321 | ✗ |

→ idle cash 제거(60%→100% 투자)로 **+116%→+215%**. **항상 top-N에 풀투자**(현금 미보유·1일 1회 리밸런싱)가 표준 운용형태이자 사용자 취지와 일치. 기본 `top_n≥5`면 `max_weight=0.2` 상한이 안 걸려 자동 100% 투자(6종목 테스트만 top_n=3로 idle 발생).

## 6. 결론 & 한계 (정직)
- **"1분 단위 매매" 가설은 기각** — 비용(특히 국내 매도세 0.20%)이 모든 1분봉 전략을 죽인다(실측 −100%). 사용자의 대규모 유니버스 선택은 옳았으나, **빈도는 1분이 아니라 일/저회전**이어야 한다.
- **유망 채택 후보: `xs_momentum` 일봉 cadence·긴 룩백(60일)·top-N 동일가중 항상 풀투자** — OOS **+215%**·PSR 0.99·저회전(124거래). 현금 미보유(1일 1회 상위 N 로테이션).
  - 트레이드오프: 상시 풀투자라 **하락장 보호 없음**(현금화 안 함). 필요 시 시장 레짐 필터(극단 하락 시 현금)를 옵션으로 추가해 long-or-cash와 절충 가능 — 단 그 자체도 검증 필요.
- **한계(라이브 전 보강 필수):** ① **6종목뿐**(횡단면은 breadth가 핵심 — 전 유니버스로 재검증) ② **다중검정**: 8개 변형을 시도했으므로 정직한 Deflated Sharpe는 N=8 보정 필요(개별 PSR 0.99보다 낮아짐) ③ **레짐 편향**: 2020~2026 반도체 강세장이 모멘텀에 유리(다른 레짐 미검증) ④ **US·point-in-time 유니버스 미검증**(생존편향).
- **다음**: 채택 후보를 **전 유니버스(KOSPI200+KOSDAQ150+S&P500+NASDAQ-100) 일봉으로 재검증**(breadth·다중검정 보정·레짐) → 통과 유지 시 라이브(일/저회전이라 분 스트리밍 불요, 기존 trade_once류 일배치 재사용 가능).
