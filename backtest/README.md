# backtest — 전략 백테스트 & 성과측정 하니스

ClickHouse `ticks`를 전역 시간순으로 replay해 매매 전략을 오프라인 시뮬레이션하고
성과지표(누적수익률·승률·MDD·Sharpe·손익비)를 산출한다. (TODO.md 0단계)

## 실행

```bash
# ClickHouse(Docker)가 떠 있고 ticks에 과거 데이터가 있어야 한다.
.venv/Scripts/python -m backtest.run \
  --symbols KRW-BTC,KRW-ETH \
  --start "2026-06-16 00:00:00" --end "2026-06-17 00:00:00" \
  --out runs/sma_baseline
```

미지정 시 전체 심볼/전체 기간을 사용한다. 결과는 표준출력 요약 + `--out` 디렉터리에
`trades.csv`, `equity.csv`, `run_meta.json`(재현용 설정·git 커밋)으로 저장된다.

## 테스트 (ClickHouse 불필요 — 합성 데이터)

```bash
.venv/Scripts/python -m unittest discover -s backtest/tests -t .
```

## 설계 (라이브와의 정합/차이)

- **결정 수학 동일**: 신호/사이징/청산 임계값은 `strategy/sma_trader.py`의 순수 함수
  (`sma_gap`/`sma_state`/`position_fraction`/`liquidation_reason`)와 상수(`MIN_ORDER_KRW`)를
  그대로 import한다. → 라이브 전략과 결정 로직 발산 없음.
- **체결/수수료/평단 동일**: `engine.matching`·`portfolio.updater`와 같은 가정
  (MARKET=시장가 즉시체결, `fee=price*qty*0.0005`, 평단=수수료 포함 취득단가).
- **가상시계**: 라이브의 `time.monotonic()`(실시간)을 tick의 `trade_ts`(시장시간)로 대체.
  warmup/cooldown/min_hold는 "시장시간 초"로 환산된다.

### 백테스트 가정 (baseline 해석 시 유의)

- **지연 = 0 이상화**: 주문이 트리거 틱 가격으로 즉시 체결된다고 가정. 라이브의 async 지연
  (전략→outbox→relay→engine)이 없으므로 약간 낙관적이다. `--slippage-bps`로 불리한 슬리피지를 부여해 보정 가능.
- **단일 계좌**: 라이브의 다계정 대신 단일 가상계좌를 시뮬레이션.
- **가격 정밀도**: ClickHouse `price`는 Float64라 라이브가 받는 Upbit 원본 문자열가와 미세한 차이가 있을 수 있다.
- **워밍업 시계**: 라이브 워밍업은 프로세스 기동 후 **벽시계** `STRATEGY_WARMUP_SEC`초지만, 백테스트는 replay 시작 후 **시장시간(trade_ts)** 기준이다. 데이터 공백 구간에서 게이트 해제 시점이 라이브와 어긋날 수 있다(영향은 replay 시작 워밍업 구간에 한정).
- **동시각 종목 간 순서**: 같은 ms(trade_ts는 ms 해상도)의 서로 다른 종목 틱은 `symbol` 사전순으로 결정적 정렬한다. seq는 종목별 카운터라 종목 간 실제 도착순(라이브 Kafka)과 다를 수 있어, 단일 계좌의 예산/최대보유 경합 해소가 라이브와 미세하게 다를 수 있다.
