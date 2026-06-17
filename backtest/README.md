# backtest — 전략 백테스트 & 성과측정 하니스

업비트 **1분봉(종가)** 을 전역 시간순으로 replay해 매매 전략을 오프라인 시뮬레이션하고
성과지표(누적수익률·승률·MDD·Sharpe·손익비)를 산출한다. (TODO 0단계)

## 1) 데이터 백필 (업비트 REST → 로컬 캐시)

```bash
# 최근 2년치 1분봉을 5대 메이저에 대해 받아 data/candles 에 캐시 (1회, 이후 재사용)
.venv/Scripts/python -m backtest.backfill --unit 1 --days 730
```

증분 재실행 가능: 캐시가 있으면 최신 방향(newest~now)과 과거 방향(oldest~cutoff)을 모두 보충한다.
중단되어도 페이지마다 영속되며 재실행 시 이어받는다(finalize는 tmp+os.replace로 원자적).

## 2) 백테스트 실행

```bash
# 업비트 캐시로 2년 백테스트 (장기엔 자산곡선 표본을 일 단위로)
.venv/Scripts/python -m backtest.run --source upbit --days 730 --sample-sec 86400 --out runs/sma_base

# 또는 ClickHouse candles_1m 사용(Docker 필요, 1분 고정)
.venv/Scripts/python -m backtest.run --source clickhouse --symbols KRW-BTC --start "2026-06-01 00:00:00"
```

결과는 표준출력 요약 + `--out` 디렉터리에 `trades.csv`, `equity.csv`, `run_meta.json`(재현용 설정·git 커밋)으로 저장된다.

## 3) 테스트 (네트워크/Docker 불필요 — 합성 데이터)

```bash
.venv/Scripts/python -m unittest discover -s backtest/tests -t .
```

## 설계 (라이브와의 정합/차이)

- **결정 수학 동일**: 신호/사이징/청산 임계값은 `strategy/sma_trader.py`의 순수 함수
  (`sma_gap`/`sma_state`/`position_fraction`/`liquidation_reason`)와 상수(`MIN_ORDER_KRW`)를 그대로 재사용.
- **체결/수수료/평단 동일**: `engine.matching`·`portfolio.updater`와 같은 가정
  (MARKET=종가 즉시체결, `fee=price*qty*0.0005`, 평단=수수료 포함 취득단가, NUMERIC(20,4) 반올림 모사).
- **가상시계**: 봉의 시작 시각(window_start)을 BTick.ts로 쓴다. 두 소스(업비트 REST/ClickHouse)가 동일 봉을
  동일 시각에 올리도록 통일(업비트 응답의 timestamp=마지막 체결시각은 쓰지 않음).

### 백테스트 가정 (baseline 해석 시 유의)

- **지연 = 0 이상화**: 주문이 봉 종가로 즉시 체결된다고 가정(라이브 async 지연 없음). `--slippage-bps`로 보정 가능.
- **단일 계좌**: 라이브 다계정 대신 단일 가상계좌. 동일 ts(분 경계)에 여러 종목이 몰리면 `(ts, symbol)` 순서로
  결정적 처리(현금/최대보유 경합) — 두 소스가 동일 규칙이라 소스 간 재현 일치.
- **가격 정밀도**: 캔들 종가는 Float64라 라이브가 받는 Upbit 원본 문자열가와 미세한 차이가 있을 수 있다.
- **워밍업 시계**: 라이브 워밍업은 프로세스 기동 후 벽시계 기준이나, 백테스트는 replay 시작 후 시장시간 기준이다.
- **시간 기반 가드**: 쿨다운/최소보유/워밍업은 틱 케이던스 기준이라 1분봉(60s/봉)에선 무뎌진다 — 분봉용 N봉 단위 재튜닝은 별도(후속).
