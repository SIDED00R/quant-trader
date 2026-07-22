# quant-trader

**주식(KR/US) ML 퀀트 자동매매**를 메인으로, 코인 추세추종을 서브로 운용하는 자동매매 시스템 — ML 챔피언(GBDT 횡단면 랭킹)으로 KR·US 주식을 주간 리밸런싱(KIS 모의 체결·체결추격 보장)하고, 수동 주문(즉시/예약)과 잔고·성과 대시보드를 제공한다. 코인은 업비트 시세 상시 수집 + **일봉 추세추종 앙상블**로 가상 자금을 매일 자동 매매한다(Kafka 기반 수집 파이프라인은 코인 데이터가 사용).

> **이 문서 하나로 전체 파악**을 목표로 한다: 무엇이 / 어디에 / 어떻게 구현돼 있고 / 어떻게 돌아가는지.
> 설계 배경·의사결정은 [DESIGN.md](DESIGN.md)·[project_flow.md](project_flow.md), 로드맵은 [TODO.md](TODO.md), 배포는 [DEPLOY.md](DEPLOY.md).

---

## 1. 한눈에

| 축 | 내용 |
|----|------|
| **핵심 흐름** | [주식] ML 스코어 → 주간 top-N 리밸런싱 → KIS 체결(추격 보장) · [코인] 수집(상시) → 집계 → 일봉 신호 → 체결 — 잔고/손익 대시보드 공통 |
| **메시지 버스** | Apache Kafka (KRaft) — 틱 1스트림을 여러 소비자에 **팬아웃** |
| **OLTP** | PostgreSQL — 계좌/주문/포지션/체결/전략가중치 (outbox 패턴) |
| **OLAP** | ClickHouse — 틱/캔들(1분·일봉)/분석 |
| **API·대시보드** | FastAPI + Grafana (+ Caddy 자동 HTTPS) |
| **전략** | [주식] LightGBM 횡단면 챔피언(KR=OHLCV+DART 펀더 / US=+13F·섹터) 주간 top-N · [KR 일목] 주봉 구름 신규 돌파 페이퍼(ML과 자산곡선 비교) · [코인] 일봉 저회전 추세추종 앙상블(5/40·10/60·20/100), walk-forward·Deflated Sharpe 검증 |
| **배포** | GCP **2-VM** — 상시 **틱 수집 VM(collector, e2-small)** + 온디맨드 **매매 VM(e2-standard-2, 자기완결·로컬 DB)**(스케줄러 8잡: 코인 매일 10:00 KST / KR 평일 15:00 KST / US 평일 15:00 ET / 데이터 유지보수 매월 첫 토 04:00 UTC + 각 스위퍼 재시도) · 매매 VM on/off·대시보드는 별도 `gcp-cost-controller`(텔레그램) 담당 · main 머지 시 CI/CD 자동배포 |
| **주식 브로커/데이터** | KIS(모의 체결 KR+US·수동주문) · 토스(일봉 데이터) · 키움(틱 아카이브 수집 전용 — 매매 미채택) |

### 운용 성과 — 시장별 자산 추이 (자동 갱신)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/SIDED00R/quant-trader/assets/equity-dark.svg">
  <img alt="시장별 평가자산 추이 — 기준일=100, 코인/국장/미장 + 전체(KRW 환산), 매매 잡 종료 시 자동 갱신" src="https://raw.githubusercontent.com/SIDED00R/quant-trader/assets/equity-light.svg" width="100%">
</picture>

각 매매 잡이 종료 시 시장별 평가자산을 `equity_snapshots`에 기록하고 이 차트를 렌더해 `assets` 브랜치로 발행한다(§8). VM이 꺼져 있어도 여기서 자산 흐름이 보이고, 대시보드 **자산 탭**에서는 기간 선택·오늘 실시간 값을 포함한 동일 곡선을 제공한다. **국장 일목(페이퍼)** 곡선은 점선·자기 시작일 앵커로 함께 표시하되 가상 자금이라 전체(TOTAL) 합산에서는 제외한다.

---

## 2. 아키텍처 & 데이터 흐름

```
[수집·저장 — 상시]
업비트 WS ─ streaming/ingester ─▶ (Kafka market.ticks) ─┬─▶ streaming/sink ─────▶ ClickHouse(ticks)
                                                        └─▶ streaming/aggregator/candle ─▶ candles_1m
                                                                    └─ aggregator/daily ─▶ candles_1d

[자동매매 — 일봉 1회(프로덕션) / 스트리밍(로컬)]
trading/strategy/runners/live_ensemble ─(Kafka strategy.signals)─▶ trading/strategy/runners/commander
   └─▶ order_outbox ─▶ trading/relay ─(Kafka orders)─▶ trading/engine ─(Kafka executions)─▶ trading/portfolio ─▶ PostgreSQL
trading/strategy/runners/trade_once  ── (프로덕션 라이브 경로: 일봉 합성 목표 → 동기 주문·체결 → PostgreSQL)

[조회] api (FastAPI 대시보드/REST) · Grafana
```

- **메시지 흐름**: `market.ticks` → (sink·candle) 팬아웃 → `candles_1m` → `candles_1d` → `strategy.signals` → `orders` → `executions`.
- **두 매매 경로**: 프로덕션은 `trade_once`(일 1회 동기 배치). 스트리밍 경로(`commander`/`relay`/`engine`/`portfolio`)는 로컬 개발·디버깅용으로 함께 존재.
- **OLTP/OLAP 분리**: 정확성 필요한 계좌·주문은 Postgres(`Decimal`/`NUMERIC`), 대량 분석은 ClickHouse(`Float64`).

---

## 3. 폴더 구조 — 어디에 뭐가 있나

폴더가 **실행 단계**를 드러낸다: `streaming/`(수집→집계) → `trading/`(신호→체결). `batch/`·`api/`·`common/`은 직교(파이프라인 단계 아님). **폴더당 하나의 기능, 같은 깊이 = 같은 성격** — `common/{broker,marketdata,chart,equity}`·`trading/strategy/{core,plugins,runners}`·`batch/{backtest,candles,rawdata,jobs,universe,features,ml}` 하위 패키지도 동일 원칙을 따른다.

### `common/` — 공용 라이브러리 (파이프라인 단계 아님, 프로덕션·배치 공용)
평면(기반 인프라) + 성격별 하위 패키지(`broker/`·`marketdata/`·`chart/`·`equity/`)로 구성.

#### `common/` (평면) — 설정·연결·직렬화 기반
| 모듈 | 역할 |
|------|------|
| `config.py` | 환경변수/런타임 설정 로딩 (pydantic-settings 타입 검증 — 잘못된 값은 기동 시 실패) |
| `constants.py` | 중복·동기화 위험 고정 상수 단일 출처(CH 컬럼리스트·HTTP 한도·KIS TR 등) |
| `schemas.py` | 이벤트 직렬화 모델(Tick/Order/Signal/Execution) |
| `kafka_client.py` · `clickhouse_client.py` · `postgres_client.py` | 각 인프라 연결 팩토리 |
| `migrations.py` | 버전 마이그레이션 러너 — `db/migrations/`의 미적용 SQL만 순차 적용 + 부팅 repair 재실행 |
| `http_client.py` | 429/5xx 지수 백오프 GET (Upbit·Toss·KIS 공용 재시도) |
| `cache.py` | JSON 파일 캐시 load/dump(핸들 누수 방지 — EDGAR·13F·섹터·DART 공용) |
| `oauth_token.py` | 스레드 안전 토큰 캐시·선제 재발급(키움/토스/KIS 공용) |
| `rate_limit.py` | 클라이언트측 레이트리밋(제공자×그룹 토큰버킷) |
| `strategy_weights.py` | strategy_weights 읽기 + 동일가중 폴백 |
| `order_writer.py` | orders + order_outbox 원자적 INSERT(outbox) |
| `notify_telegram.py` | 텔레그램 매매 알림 전송(텍스트/사진, MTProto 사용자 세션, 절대 raise 안 함) — CLI 겸용(`python -m common.notify_telegram "msg"`) |
| `kafka_watchdog.py` | 프로듀서 배달 정체 워치독(`DeliveryWatchdog`) — "produce는 되는데 성공 배달 없음"이 180s 지속되면 수집기가 SystemExit → docker 재시작 위임(업비트·키움 수집기 공용, 무틱 구간 오탐 없음) |

#### `common/broker/` — 브로커 클라이언트(키움·토스·KIS)
| 모듈 | 역할 |
|------|------|
| `kiwoom_client.py` · `toss_client.py` · `kis_client.py` | 키움/토스/KIS OAuth2 토큰(공용 `oauth_token` 사용) |
| `kis_account.py` | KIS 국내·해외 잔고 조회 |
| `kis_order.py` | KIS KR/US 단건 모의 주문(운영) — 무재시도, 단 EGW00201(게이트웨이 한도 거부=미접수)만 백오프 재시도 |
| `kis_cancel.py` | KIS 해외 미체결 주문 취소(체결 추격용, ODNO 기반) |
| `kis_chase.py` | 주문 체결 추격(`place_and_chase`) — 잔고 diff 확인·미체결 취소·버퍼 확대 재주문 |
| `kis_balance.py` | KIS KR/US 모의계좌 잔고 정규화(대시보드용 읽기 전용) |
| `kis_overseas_price.py` | US 티커 → 현재가 + 주문용 거래소코드(NASD/NYSE/AMEX) 해석 |
| `kis_domestic_price.py` | KR 종목 현재가(수동주문 금액→수량 환산용) |

#### `common/marketdata/` — 시세·캔들·거래일·종목메타
| 모듈 | 역할 |
|------|------|
| `candles.py` | candles_1d 종가 스트림(프로덕션 안전 — backtest 비의존) |
| `market_hours.py` | 심볼→자산군 판정 + 정규장 개장시간(KRX·US, DST) + 자산군 인지 연율화(`periods_per_year`) |
| `market_holidays.py` | 거래소 휴장일·로컬 거래일 판정(주간 리밸런싱 휴장 게이트 — NYSE 하드코딩, KR은 체결기반 재시도 위임) |
| `symbols.py` · `upbit_markets.py` | 거래 종목 목록 해석 / 업비트 마켓 메타 |
| `upbit_ticker.py` | 업비트 공개 REST 현재가({심볼:체결가}) — `trade_once` 마크·주문가(틱 DB 비의존 → 수집 VM 디커플링) |
| `stock_price.py` · `stock_ohlc.py` | 주식 최신 일봉 종가 / 일봉 OHLCV 이력 조회(ClickHouse, 수량 산정·일목 신호·시세 폴백 공용) |
| `ichimoku.py` | 일목균형표 순수 지표(일봉→주봉 리샘플·전환/기준/선행스팬·최신 신호 — 진입은 직전 완결봉 비교 **신규 돌파 이벤트**) — batch 비의존(app 공용) |
| `toss_daily.py` | 토스 일봉 fetch(REST, 401 자가재발급) — app 이미지 공용(/차트 봇 온디맨드). CH 적재는 `batch.candles.toss_daily_load` |
| `stock_names.py` | 종목명↔티커 조회(repo 번들 `refdata/stock_names.json` 로드 — FDR로 KR 전종목+US 1회 생성, 런타임 외부호출 0)·질의 해석·CH `stock_names` 적재 — /차트 봇·관심종목 검색용(정확 티커는 사전 무관 동작). 갱신=`scripts/refresh_stock_names.py` |

#### `common/chart/` — 차트 렌더·텔레그램 발송
| 모듈 | 역할 |
|------|------|
| `candle_chart.py` · `symbol_chart.py` | 캔들+일목 구름 PNG 렌더(pillow, ASCII) / 종목 1개→차트+한글 캡션 조립(KR 주봉+일목·US 일봉). KR 조회 깊이 상수 `KR_FETCH_DAYS`(표시 104주 전 구간 구름에 필요한 lookback 포함) 단일 출처 |
| `watchlist_chart_telegram.py` | 관심종목 데일리 차트 푸시(전 계정 합집합·KR 우선·상한 20 → symbol_chart → 유저세션 발송) — 코인 잡 훅(하루 1회) |
| `equity_chart_telegram.py` | 자산 차트 PNG 렌더(pillow)+텔레그램 사진 발송 — 코인 데일리 잡 훅, 하루 1장(비치명) |

#### `common/equity/` — 자산 시계열
| 모듈 | 역할 |
|------|------|
| `equity_snapshot.py` | 시장별(COIN/KR/US) 평가자산 스냅샷 upsert — 매매 잡 종료부 훅(비치명, `equity_snapshots` 하루 1행). KR 일목 페이퍼=`account_id='kr_ichimoku'` |
| `equity_series.py` | 자산 시계열 조회·정규화·전체(KRW 환산, FRED usdkrw) 합성 — `/equity/history`·차트 렌더 공용 |

### `streaming/` — 연속 데이터: 수집 → 적재 → 집계
| 모듈 | 역할 |
|------|------|
| `ingester/upbit_ws.py` | 업비트 WS 실시간 체결 → `market.ticks` (배달 워치독 — 성공 배달 180s 없으면 자가종료→재시작) |
| `ingester/stock_kiwoom.py` | 키움 실시간 주식체결 → `stock.ticks` (동일 워치독) |
| `sink/tick_clickhouse.py` · `sink/stock_tick_clickhouse.py` | ticks → ClickHouse 적재 |
| `sink/_parse.py` | 틱 JSON → CH 행 변환(두 싱크 공용 — COLUMNS_TICKS 순서 단일 출처) |
| `aggregator/candle.py` | `market.ticks` → 1분봉 `candles_1m` |
| `aggregator/daily.py` | `candles_1m` → 일봉 `candles_1d` 리샘플 |

### `trading/` — 신호 → 체결
`strategy/`는 프레임워크(`core/`)·전략 구현(`plugins/`)·엔트리포인트(`runners/`)로 3분할. `engine/`·`portfolio/`·`relay/`는 불변.

#### `trading/strategy/core/` — 전략 프레임워크(공용)
| 모듈 | 역할 |
|------|------|
| `base.py` | 전략 인터페이스 + 실행 어댑터 프로토콜 |
| `indicators.py` | 기술 지표 순수 함수(SMA/RSI/MACD/BB/ATR…) |
| `registry.py` | 이름 → 전략 클래스 조회 |
| `rebalance.py` | 목표비중→주문 결정·합성·매수 사이징(`affordable_qty`) 순수함수(commander·ensemble·trend·백테스트 횡단면 공유, DB 무관) |
| `weight_policy.py` · `decision_record.py` | 가드된 가중치 산출 / 매매결정 분류 |
| `notify_messages.py` | 매매 잡 결과 → 텔레그램 알림 문안 조립(순수 함수 — 코인/KR/US 공용) |

#### `trading/strategy/plugins/` — 전략 구현체
| 모듈 | 역할 |
|------|------|
| `trend.py` · `trend_signal.py` | 저회전 추세추종 + 변동성 타게팅(히스테리시스 래치) |
| `ensemble.py` | 다중 추세속도 앙상블 합성 목표비중 |
| `sma.py`·`rsi.py`·`macd.py`·`bollinger.py`·`breakout.py`·`disciplined.py` | 개별 지표 전략(+공통 규율 베이스) |
| `cross_sectional.py` | 횡단면 랭킹 전략(`xs_reversal`/`xs_momentum`, 봉 단위 상위N 동일가중 long-or-cash) |
| `intraday.py` | 세션 기준 인트라데이 전략(`orb`/`intraday_momentum`, 오버나잇 미보유 long-or-cash) |
| `sma_trader.py` | SMA 순수 판정 함수(신호·사이징·청산 임계 — `sma`/`disciplined` 공용; 과거 라이브 틱봇 run 루프는 앙상블 경로로 대체·제거) |

#### `trading/strategy/runners/` — 엔트리포인트
| 모듈 | 역할 |
|------|------|
| `live_ensemble.py` | 일봉 마감마다 부하별 목표비중 신호 발행 → `strategy.signals` |
| `commander.py` | `strategy.signals` 소비 → 부하 가중합 목표로 모의주문 |
| `trade_once.py` | **프로덕션 라이브 경로** — 일봉 목표로 동기 주문·체결 후 종료 |
| `stock_trade_once.py` · `us_trade_once.py` | 주식 ML 챔피언 top-N 주간 모의 리밸런싱(KR=이탈 전량 매도→신규 매수 시장가, 체결확인 15:35 KST 데드라인(동시호가 매칭 커버) / US=해외 지정가+체결추격 `kis_chase`·거래소 라우팅) *(Dockerfile.batch — trade 프로파일, batch.ml 의존)* |
| `stock_trade_common.py` | 위 KR/US trade-once 공통부(매매계획·잔고 폴링 체결확인) |
| `kr_ichimoku_trade_once.py` | KR 일목 주봉 구름 돌파 **페이퍼 매매**(진입=**신규 돌파 이벤트** — 종가>구름상단 AND 전환>기준 AND 직전 완결봉은 구름 위 아님 / 청산=전환선 데드크로스 / 캡 30·돌파강도 우선). 실주문 없이 시뮬 장부 기록, ML 전략과 자산곡선 병행 비교. 순수 결정부 `plan_trades`. 매매 후 **신규 매수 종목의 주봉+일목 차트를 텔레그램 사진으로 발송**(`send_entry_charts`, /차트 봇 렌더러 재사용 — 차트용은 `KR_FETCH_DAYS` 깊이 재조회로 전 구간 구름). *(Dockerfile.batch — trade 프로파일)* |
| `weekly_marker.py` | 주간 리밸런싱 멱등 마커(ISO 주차별 완료 기록·조회, `weekly_rebalance` — 평일 재시도에도 주 1회 보장) |

#### `trading/engine/` · `trading/portfolio/` · `trading/relay/` — 불변
| 모듈 | 역할 |
|------|------|
| `engine/matching.py` | 주문 매칭(시장가/지정가) → `executions` |
| `portfolio/updater.py` | `executions` → Postgres 잔고/포지션 |
| `portfolio/account_read.py` | 자동매매 계정·보유수량·현금 조회(trade_once·commander 공용) |
| `portfolio/paper_ledger.py` | 주식 페이퍼(모의) 체결 — 실주문 없이 `apply_execution` 재사용해 시뮬 장부 기록(매도 거래세 포함), 일목 페이퍼 전략용 |
| `relay/order_relay.py` | order_outbox → `orders` 토픽 발행 |

### `batch/` — 오프라인/배치 (프로덕션 이미지 제외, `Dockerfile.batch`)
백테스트 코어(`backtest/`)·시세 수집(`candles/`)·외부원본 수집(`rawdata/`)·운영 잡(`jobs/`)·유니버스(`universe/`)·피처(`features/`)·모델링(`ml/`)로 분리.

#### `batch/backtest/` — 백테스트 코어
| 모듈 | 역할 |
|------|------|
| `run.py` | 백테스트 실행 CLI |
| `engine.py`·`account.py`·`fills.py`·`models.py` | 봉 replay 엔진·계좌·체결모델·값타입(주식=정수 주 단위, 국내주식 매도 거래세 반영) |
| `datasource.py` | ClickHouse 캔들 replay |
| `metrics.py`·`walkforward.py`·`report.py` | 지표·walk-forward(Deflated Sharpe)·리포트 |

#### `batch/candles/` — 시세 수집·백필
| 모듈 | 역할 |
|------|------|
| `upbit_candles.py`·`upbit_daily.py`·`toss_daily_load.py`·`toss_intraday.py` | Upbit 분봉/일봉·Toss 일봉 CH 적재(fetch는 `common.marketdata.toss_daily`)/분봉 수집 |
| `_upsert.py` | 캔들 CH 적재 코어(멱등 가드·행수 반환 — 위 세 적재기 공용) |
| `backfill.py`·`backfill_daily.py`·`backfill_stock_daily.py`·`backfill_stock_intraday.py`·`csv_to_clickhouse.py` | 백필 CLI들(코인·주식 일봉/분봉, CSV→CH) |
| `selective_stock_backfill.py` | 일봉 데이터 갭·수정주가 재조정 감지 종목만 선별 재백필(매월 유지보수의 전체 재백필 대체) |
| `refresh_stock_daily.py` | 매매 전 일봉 증분 갱신(활성 종목, 종목별 격리·시간상한) |

#### `batch/rawdata/` — 외부데이터 영구수집
| 모듈 | 역할 |
|------|------|
| `fundamentals.py`·`fred.py`·`us_membership.py`·`sec_13f.py`·`sec_sector.py`·`insider.py`·`earnings.py`·`factor_returns.py`·`finra_short.py`·`krx.py`·`krx_bulk.py`·`kr_index_membership.py`·`kr_fundamentals.py`·`kr_delisted.py`·`_krx_session.py` | **외부데이터 영구수집**(멱등·일별증분): SEC EDGAR 펀더멘털·FRED 매크로·US 지수 PIT멤버십·13F 기관보유·SEC SIC 섹터·내부자거래(SEC Form 4)·실적발표일(SEC 8-K Item 2.02)·팩터 일간수익률(Ken French)·US 공매도 잔고(FINRA)·KR 수급/공매도/외국인보유(KRX, `krx_bulk`=고속 by-date)·KR 지수 PIT멤버십·KR 펀더멘털(DART)·KR 상장폐지+상폐 OHLCV(FDR, 생존편향 보정)·공용 KRX 로그인 세션(`_krx_session`)·공용 숫자 파싱(`_parse`) |

#### `batch/jobs/` — 운영 잡
| 모듈 | 역할 |
|------|------|
| `maintenance_once.py`·`verify_freshness.py` | 정기 데이터 유지보수 1회 실행 — 활성 유니버스 일봉 선별 재백필(재조정/데이터갭 종목만) + 위 분기/월간 수집기(EDGAR·13F·SIC·DART·내부자·팩터·US공매도·실적) 재실행 + **연구 데이터 지속 수집**(KRX 수급·공매도·외국인보유 증분, KR/US 지수 PIT 멤버십, FRED 매크로, KR 상폐 메타 — 모델 미사용이어도 재사용 자산으로 축적) + 마지막에 **데이터 신선도 점검**(`verify_freshness`, 읽기전용: 테이블별 행수·최신일자·지연일; 임계 테이블 낡음/빔 → 🔴), 매월 첫 토요일 04:00 UTC |
| `reeval_weights.py` | 부하 OOS 성과 → strategy_weights 갱신 — **수동 전용·스케줄 미배선**(`ENSEMBLE_ADAPTIVE=false` 기본이라 라이브는 균등가중, 휴면 설계 자산) |

#### `batch/universe/` — 지수 구성종목 유니버스
| 모듈 | 역할 |
|------|------|
| `kospi200.txt`·`kosdaq150.txt`·`sp500.txt`·`nasdaq100.txt` | 백테스트/연구용 종목 유니버스 목록(`backfill_stock_daily.py --symbols-file` 입력) — 현재 구성 기준 스냅샷이라 생존편향 존재(상세 `batch/universe/README.md`) |

#### `batch/features/` — ML 피처
| 모듈 | 역할 |
|------|------|
| `ohlcv.py`·`edgar.py`·`cross_market.py`·`kr_microstructure.py`·`compute.py` | **ML 피처**: OHLCV 파생(~58)·EDGAR 펀더멘털/13F 일별파생·누설없는 US 컨텍스트(KR 모델용)·KR 미시구조(수급·공매도·외국인보유 일별파생)·저장 |

#### `batch/ml/` — ML 모델링
| 모듈 | 역할 |
|------|------|
| `dataset.py`·`cv.py`·`evaluate.py`·`baseline_lgbm.py`·`stock_score.py` | **ML 모델링**(주식 횡단면 수익예측): 피처+라벨 조립·purged/embargo CV·Rank IC/ICIR/NW-t 평가·LightGBM(lambdarank, 챔피언)·라이브 트레이더용 최신 거래일 챔피언 스코어러(`stock_score`). 시장별(US/KR) 분리. (DL 비교실험 GRU/MLP·GBDT 튜닝 CLI·IC 단독 테스트 CLI는 역할 종료로 코드 삭제 — 기록은 `docs/ml_models_research.md`·`ml_progress.md`) |

### 그 외
| 폴더 | 역할 |
|------|------|
| `tests/` | 단위테스트(소스 트리 미러: `tests/{backtest,candles,trading,common,api,scripts}/`) |
| `api/` | FastAPI 대시보드/REST — `main.py`(앱), `security.py`/`auth_google.py`(인증), `cache.py`/`warmup.py`(TTL 캐시+기동 예열), `stock_order_executor.py`(수동주문 예약 실행기), `telegram_bot.py`(/chart 봉차트 봇 — 수집 VM 상시, Bot API long-poll), `routes/`(account·orders·market·history·performance·strategy·decisions·equity·autotrade·stocks·stock_orders·rebalance·watchlist·health·web) |
| `scripts/` | `init_db.py`(미적용 마이그레이션 적용 + repair)·`reset_account.py`(모의 계정 리셋)·`render_equity_chart.py`(자산 차트 SVG 렌더 — stdlib, assets 브랜치 발행용)·`telegram_login.py`(텔레그램 StringSession 1회 발급) |
| `db/` | `migrations/{postgres,clickhouse}/NNNN_*.sql`(버전 마이그레이션 — `common/migrations.py`가 미적용분만 순차 적용)·`postgres_repair.sql`(매 부팅 재실행 자가회복 백필)·`clickhouse_system_logs.xml`(CH system 로그 최소화 오버라이드 — 에러만 7일, 프로파일링 비활성). **스키마 변경은 새 `NNNN_*.sql`로 추가**(baseline·기존 파일 수정 금지, forward-only) |
| `infra/` | GCP 기동 스크립트(`collector-vm-startup.sh`=틱 수집 상시 · `trade-vm-startup.sh`=온디맨드 자기완결·대시보드 모드·절대 워치독) + 수집 VM 헬스체크(`collector-healthcheck.sh` — cron 30분: 디스크·컨테이너·틱 유입 → 텔레그램, **코인 틱 정지 시 kafka+스트림 자동 재기동**(3h 쿨다운)) + 배포(`deploy-collector-vm.sh`) + `fix-volume-ownership.sh`(non-root 컨테이너 uid 1000용 볼륨/바인드 소유권 정렬 — compose up 전 멱등 실행) + CI/CD·스케줄러 1회 셋업(`setup-cicd.sh`). VM on/off·대시보드는 별도 `gcp-cost-controller` 프로젝트(텔레그램)가 담당 |
| `.github/workflows/` | `deploy.yml` — main 머지 시 이미지 빌드→Artifact Registry→VM 자동배포(배포 검증=이미지 sha 라벨 대조) |
| `dashboard/` | Grafana 프로비저닝/대시보드 |
| `tools/` | 개발용 단독 도구(`ch_browser.html` — ClickHouse 쿼리 브라우저) |
| `docs/` | 설계·전략·모델·한도 등 심화 문서 |

---

## 4. 어떻게 돌아가나 (실행 순서·의존)

선행관계(A 완료 → B 가능):
1. `scripts.init_db` → 모든 서비스 (스키마 생성 선행)
2. `streaming.ingester` → `streaming.sink` · `streaming.aggregator` (market.ticks 흐름)
3. `aggregator.candle` → `aggregator.daily` (candles_1m → candles_1d)
4. `aggregator.daily` → `trading.strategy.runners.live_ensemble` (candles_1d 워밍업)
5. `live_ensemble` → `commander` → `relay` → `engine` → `portfolio` (orders → executions → 잔고)
6. candles_1d 완성 → `trading.strategy.runners.trade_once` (일 1회 온디맨드 배치)
7. candles_1d(전기간) → `batch.jobs.reeval_weights` (가중치 재평가 — 수동 전용·스케줄 미배선, 라이브는 균등가중)
8. `stock_trade_once`·`us_trade_once`: 가드(주간마커·휴장) → `refresh_stock_daily`(일봉 증분 갱신) → 스코어링(신선도·커버리지 게이트) → 매도(KR 이탈 전량)·매수 발주 → 데드라인 체결확인(KR ≤15:35 KST)

**docker-compose 프로파일**(서비스 코드는 동일, 실행 묶음만 다름):
- `app` — 로컬 풀스택(수집+스트리밍 매매+대시보드)
- `collector` — 상시 틱 수집 VM(Kafka+ClickHouse+Postgres+WS 레코더 + 텔레그램 `/chart` 봇; 대시보드·매매 제외 → e2-small)
- `data` — (구) 데이터 VM 풀 서브셋(수집·저장·대시보드) — 로컬/레거시용
- `trade` — 온디맨드 매매(코인 `trade_once` + KR/US 주식 `stock_trade_once`·`us_trade_once` + KR 일목 페이퍼 `kr-ichimoku-trade-once` + `maintenance_once` + 자산 차트 `equity-chart` + 관심종목 차트 `watchlist-charts`); **로컬 DB 자기완결**(터널 없음)
- `batch` — 배치 러너(`reeval` 서비스, `Dockerfile.batch`): 수동 `reeval_weights` + startup의 크립토 `backfill_daily` 실행에 재사용

---

## 5. 데이터 소스 & 브로커

| 제공자 | 용도 | 상태 |
|--------|------|------|
| **업비트** | 코인 실시간 틱(수집 VM) + 분/일봉 백필 + **현재가 REST**(`trade_once` 마크·주문가) | 운영 |
| **키움** | 주식 실시간 틱(`stock.ticks`) 아카이브 수집 + 인증 | 수집 전용(매매 미채택 — 체결은 KIS) |
| **토스** | 주식 **일봉 데이터**(백테스트 입력, KR+US) + 라이브 매매 전 일일 증분 갱신 소스 | 운영(데이터 전용, WS 미지원) |
| **KIS(한국투자)** | 주식 **모의 체결**(KR+US 통합) | 운영(토큰·잔고·KR/US 단건 모의 주문, 평일 체결검증 진행) |
| **SEC EDGAR** | US 펀더멘털·공시·13F 기관보유·내부자거래(Form 4)·실적발표일(8-K 2.02) | 운영(키리스 API, point-in-time) |
| **Ken French Data Library** | Fama-French 5팩터+모멘텀 일간 수익률(수익 귀인·팩터중립 피처) | 운영(키리스 zip) |
| **FINRA** | US 공매도 잔고(격주 통합, `stock_short` market='US') | 운영(키리스 CDN) |
| **FRED** | 매크로(금리·VIX·환율·유가 — 연구 데이터) | 운영(월간 유지보수, API 키) |
| **GitHub(fja05680/sp500)** | US 지수 PIT 멤버십·편입편출 | 운영(월간 유지보수, 생존편향 부분해결) |
| **KRX(pykrx)** | KR 외국인/기관 수급·공매도·외국인보유·지수 PIT(연구 데이터) | 운영(월간 유지보수 증분, 로그인·`batch/rawdata/krx.py`) |
| **DART** | KR 펀더멘털(ML 피처) | 운영(`batch/rawdata/kr_fundamentals.py`) |
| **FDR(FinanceDataReader)** | KR **상장폐지** 종목·상폐 OHLCV(ML 생존편향 보정) | 운영(월간 유지보수=메타, 상폐 OHLCV는 수동 1회, `batch/rawdata/kr_delisted.py`) |

> **ML 데이터**(`batch/rawdata`→`fundamentals_quarterly`·`macro_daily`·`index_membership`·`institutional_13f`·`stock_investor_flow`·`stock_foreign_holding`·`stock_short`(KR+US)·`insider_transactions`·`earnings_calendar`·`factor_returns_daily`)는 모델 검증과 무관하게 영구 저장(재사용 자산·PIT 소실 방지). 상세 [docs/ml_data_acquisition.md](docs/ml_data_acquisition.md).
> 분업: **데이터는 토스/업비트, 체결은 KIS(주식)/시뮬(코인·KR 일목 페이퍼)**. 호출 한도는 `common/rate_limit.py`로 일원화([docs/rate_limits.md](docs/rate_limits.md)).
> 체결 비용 모델: 코인=수수료만, **국내주식=수수료+매도 거래세(`STOCK_SELL_TAX_RATE`, 기본 0.20%)**, 주식 주문은 **정수 주 단위**(`common/marketdata/market_hours.py` 자산군 판정 기준).

---

## 6. 전략 & 백테스트

- **채택 전략**: 일봉 저회전 추세추종 **앙상블**(5/40·10/60·20/100 다중 속도) + 변동성 타게팅. 과매매·수수료 출혈을 피하려 1분봉→일봉으로 전환한 결과(`project_flow.md`).
- **검증**: walk-forward(롤링 IS/OOS) + **Deflated Sharpe**(시도 횟수 페널티). 모델 카드 [docs/model.md](docs/model.md), 베이스라인 [docs/baseline.md](docs/baseline.md).
- **실행**: `python -m batch.backtest.run --source clickhouse --strategy ensemble ...` / `python -m batch.backtest.walkforward ...` (상세 [batch/backtest/README.md](batch/backtest/README.md)).
- **주식 백테스트**: `--source clickhouse --ch-table stock_candles_1d --symbols 005930 --sample-sec 86400`(토스 일봉 적재본). 주식은 정수 주 단위·국내 매도 거래세가 체결/비용에 반영된다(§5).
- **인트라데이(분봉) 연구·검증**: 후보(횡단면 `xs_reversal`/`xs_momentum` · 세션 `orb`/`intraday_momentum`)를 walk-forward+DSR+비용게이트로 검증. 방법 전수조사 [docs/intraday_research.md](docs/intraday_research.md), 검증 가이드·게이트 [docs/intraday_baseline.md](docs/intraday_baseline.md)(`--ch-table stock_candles_1m`).
- **일목균형표 주봉 전략 연구**: KR/US 백테스트·튜닝·비용/알파 검증 → **국장만 실질 α(+8~11%)** 확인(미장은 사실상 베타), 국장 페이퍼 매매로 채택(§3 `kr_ichimoku_trade_once`). 전체 기록 [docs/ichimoku_research.md](docs/ichimoku_research.md).

---

## 7. 로컬 실행 (퀵스타트)

```bash
cp .env.example .env                       # 1) 환경변수 (브로커 키 등은 .env에만, gitignore)
docker compose up -d                       # 2) 인프라(Kafka+Postgres+ClickHouse) + 토픽 생성
.venv/Scripts/python -m scripts.init_db    # 2-1) DB 스키마 1회 적용
docker compose ps                          # 3) 상태 확인

# 풀스택/데이터/매매/배치 (프로파일)
docker compose --profile app  up -d --build
docker compose --profile data up -d --build
docker compose --profile trade run --rm trade-once python -m trading.strategy.runners.trade_once
docker compose --profile batch run --rm reeval

# 개별 워커 디버깅(예)
.venv/Scripts/python -m streaming.ingester.upbit_ws
.venv/Scripts/python -m trading.portfolio.updater
.venv/Scripts/python -m uvicorn api.main:app --port 8000

# 테스트 & 린트 (dev 도구: pip install -r requirements-dev.txt)
.venv/Scripts/python -m pytest tests/ -q
.venv/Scripts/python -m ruff check .
```

접속(보안상 `127.0.0.1` 루프백 바인딩): 대시보드 `127.0.0.1:8000` · Kafka `:9092` · PostgreSQL `:5432` · ClickHouse `:8123` · Grafana `:3000`. 대시보드는 2초 폴링, 시각은 KST 표시(UTC 저장).

---

## 8. 배포

GCP **2-VM** — 상시 수집 VM(e2-small, 수집·저장만) + 온디맨드 매매 VM(e2-standard-2, 자기완결 로컬 DB, 대시보드 모드 겸용). Cloud Scheduler **8잡**(메인 4+기동실패 재시도 스위퍼 4)이 같은 VM을 기동(부팅 시각으로 분기): 코인 **매일 10:00 KST**, KR 주식 **평일 15:00 KST**, US 주식 **평일 15:00 ET**(마감 1시간 전, DST 자동), 데이터 유지보수 **매월 첫 토요일 04:00 UTC**. 각 잡 후 자가 종료(상시 비용 ~$13/월). **main 머지 = 자동 배포**(GitHub Actions → Artifact Registry → 양 VM). 절차는 [DEPLOY.md](DEPLOY.md).

**매 실행 텔레그램 통보**: 각 매매 잡이 결과(매수 내역 전부 또는 '매매하지 않음'+사유)와 오류를 텔레그램(MTProto, `common/notify_telegram`)으로 발송한다 — 스킵 실행도 발송하므로 **알림 부재 = 장애 신호**. 코인 데일리 잡은 **자산 곡선 차트(PNG 사진) 1장/일**도 함께 발송한다(`common/chart/equity_chart_telegram`) — VM·대시보드 없이 텔레그램에서 자산 흐름 확인. 잡이 뜨기도 전에 죽는 실패(이미지 빌드 등)는 startup의 `notify_fail` 폴백이 로그 꼬리와 함께 발송(exit 70='파이썬이 이미 통보' 센티널). 자격증명은 Secret Manager `telegram-env`(발급: `scripts/telegram_login.py`). **수동주문 실패**(예약 실행 포함, `api/stock_order_executor`)와 **CI 배포 실패**(`notify-failure` 잡)도 같은 채널로 통보된다. 코인 잡은 **일봉 신선도 게이트**(최신봉 3일 초과 시 매매 대신 오류 통보 — 주식 경로의 7일 게이트와 동일 원칙)를 거친다.

**자산 곡선·README 차트**: 각 매매 잡이 종료 시 시장별 평가자산을 `equity_snapshots`에 하루 1행 upsert한다(스위퍼 재실행=마지막 승리, 주간 리밸런싱 스킵 날도 KIS 잔고 재조회로 기록, 실패는 비치명). 차트(README·텔레그램·대시보드 자산 탭)는 **전 시장 공통 시작일=0% 수익률 기준**(수익 +/손실 −, 이전 이력은 기준값 산출에만 사용)으로 그린다. 잡 후 startup이 `equity-chart` 컨테이너로 SVG(라이트/다크)를 렌더해 **orphan `assets` 브랜치에 단일 커밋 force-push**하고 README 상단 `<picture>`가 이를 참조한다 — 브랜치 크기는 SVG 2개로 고정되고 `deploy.yml`(main 전용)은 발화하지 않는다. '전체(KRW 환산)' 시리즈용 환율(FRED usdkrw)은 코인 데일리 잡이 일 1회 갱신한다. 쓰기 배포키(`github-push-key`) 미준비 시 push만 조용히 스킵 — 셋업 런북은 [DEPLOY.md](DEPLOY.md).

**비용·수집 가드**: 원시 틱(ticks·stock_ticks)은 **TTL 180일**(집계 candles_1m/1d는 무기한 — 기존 테이블 적용은 DEPLOY.md의 1회 ALTER 런북), 전 컨테이너 **도커 로그 로테이션**(20m×3), 매매 VM **절대 워치독**(`shutdown -P` 90분, 대시보드 120분·유지보수 360분 — 행이어도 과금 상한), 수집 VM **헬스체크 cron**(30분: 디스크·컨테이너·틱 유입 정지 → 텔레그램, 6h 쿨다운).

---

## 9. 기술 스택 & 관련 문서

Python 3.13 · confluent-kafka · FastAPI/uvicorn · psycopg(Postgres) · clickhouse-connect · httpx · websockets · Docker Compose · Grafana · Caddy.

- 설계/스키마: [DESIGN.md](DESIGN.md) · 시스템 근거·교훈: [project_flow.md](project_flow.md)
- 배포(GCP 2-VM): [DEPLOY.md](DEPLOY.md) · 로드맵: [TODO.md](TODO.md)
- 전략·모델: [docs/model.md](docs/model.md)·[docs/baseline.md](docs/baseline.md)·[docs/algorithms.md](docs/algorithms.md)·[docs/ichimoku_research.md](docs/ichimoku_research.md)(일목 주봉 전략 연구)
- **주식 ML 파이프라인**: [docs/ml_features_research.md](docs/ml_features_research.md)(피처 카탈로그)·[docs/ml_data_acquisition.md](docs/ml_data_acquisition.md)(외부데이터)·[docs/ml_models_research.md](docs/ml_models_research.md)(모델 SOTA·ablation)·[docs/intraday_baseline.md](docs/intraday_baseline.md)(유니버스 검증)
- 키움: [docs/kiwoom.md](docs/kiwoom.md) · API 호출 한도: [docs/rate_limits.md](docs/rate_limits.md)
- 백테스트 사용법: [batch/backtest/README.md](batch/backtest/README.md)

## 알려진 한계 (학습용 MVP)
- **체결 엔진 단일 인스턴스 전제**: 최신가·pending 인메모리라 컨슈머 그룹 스케일아웃 시 깨짐.
- **모의 체결(코인)**: 사용자 간 호가 매칭 없이 실시간 최신가로 체결.
- **정밀도**: 계좌·주문은 `Decimal`/`NUMERIC` 무손실, ClickHouse 분석용은 `Float64`.
