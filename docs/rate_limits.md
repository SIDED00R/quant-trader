# API 호출 한도 분석 (Upbit · Toss · KIS · Kiwoom)

여러 제공자를 함께 쓰면서 각자 다른 호출 한도가 생겼다. 한도 초과 시 `429`로 데이터 누락·주문 실패가 난다. 이 문서는 제공자별 한도를 정리하고, 통합 레이트리미터(`common/rate_limit.py`)의 기본값 근거를 남긴다.

## 요약 표

| 제공자 | 프로젝트 용도 | 그룹/구분 | 초당 | 분당 | 한도 기준 | 신뢰도 |
|--------|--------------|-----------|------|------|-----------|--------|
| Upbit | 코인 데이터 | 시세(Quotation) | **10** | 600 | IP | 공식 |
| Upbit | (미사용) | 주문(order) | **8** | 200 | API Key | 공식 |
| Upbit | 코인 데이터 | 주문 외 Exchange | **30** | 900 | API Key | 공식 |
| Upbit | 코인 틱 | WebSocket | **5** (요청/연결) | — | IP | 공식 |
| KIS | 주식 체결 | REST(모의) | **5** | — | appkey | 공식 |
| KIS | 주식 체결 | REST(실전) | **20** | — | appkey | 공식 |
| KIS | (미사용) | WebSocket | 등록 종목 수 제한(≈41) | — | 세션 | 보조 |
| Toss | 주식 데이터 | AUTH | **5** | — | client | 관측 |
| Toss | 주식 데이터 | MARKET_DATA_CHART | **5** | — | client | 관측 |
| Toss | 주식 데이터 | MARKET_DATA 외 | 응답 헤더로 확인 | — | client | 동적 |
| Kiwoom | 틱 아카이브 수집(매매 미채택) | REST (TR별) | **≈1** (버스트 2) | — | TR/계정 | 추정 |
| Kiwoom | 틱 아카이브 수집(매매 미채택) | WebSocket | 등록 제한 | — | 세션 | 보조 |
| Telegram | 매매 알림 발송 | send(MTProto) | **1** | ~20(채팅당) | 계정/채팅 | 공식(보수) |

> 통합 레이트리미터 기본값은 위 "초당" 열을 보수적으로 채택했다. KIS는 모의(5) 기준이며 실전 전환 시 `acquire("kis","rest", rate=20)`. Telegram은 매매 잡당 1건(하루 수 건)이라 1/s로 충분하며, 초과 시 서버가 FloodWait을 주고 `notify_telegram`이 흡수한다.

## 제공자별 상세

### Upbit (코인 — 데이터/틱)
- **시세(Quotation)**: 초당 10, 분당 600. **IP 기준**.
- **주문(order)**: 초당 8, 분당 200. API Key 기준. (프로젝트는 미사용 — 시뮬 체결)
- **주문 외 Exchange**: 초당 30, 분당 900. API Key 기준.
- **WebSocket**: 초당 5(요청/연결). 틱 수집기는 연결 1개 유지라 영향 적음.
- 응답 헤더 `Remaining-Req: group=<g>; min=<남은 분>; sec=<남은 초>`로 실시간 확인.
- 출처: [업비트 Rate Limit 정책](https://docs.upbit.com/kr/reference/rate-limits)

### KIS 한국투자증권 (주식 — 체결)
- **REST**: **모의 초당 5건 / 실전 초당 20건** (appkey 기준).
- 모의는 한도가 낮아 연속 호출(파라미터 최적화 등)엔 부적합 — 단건 조회/일배치엔 충분.
- WebSocket은 별도 정책(등록 종목 수 제한 ≈41) — 본 프로젝트는 데이터를 토스로 받으므로 미사용.
- 출처: [KIS API 유량제한 쓰로틀링](https://hky035.github.io/web/kis-api-throttling/), [초당 20건 제한 대응](https://tgparkk.github.io/robotrader/2025/10/09/robotrader-1-70stocks-problem.html)

### Toss 토스증권 (주식 — 데이터)
- 한도는 **Rate Limits Group 별**: `AUTH`, `MARKET_DATA`, `MARKET_DATA_CHART`, `STOCK`, `MARKET_INFO`, `ACCOUNT`, `ASSET`, `ORDER`, `ORDER_HISTORY`, `ORDER_INFO`.
- 캔들은 `MARKET_DATA_CHART`(부하 특성상 시세와 분리) — **관측값 초당 5**. `AUTH`도 **관측 초당 5**.
- 정확값은 응답 헤더 `X-RateLimit-Limit / -Remaining / -Reset` 로 확인(동적). 토큰 1개/클라이언트(재발급 시 이전 토큰 무효).
- 출처: 토스 Open API OpenAPI 스펙(`servers: openapi.tossinvest.com`) + 본 프로젝트 실측.

### Kiwoom 키움증권 (주식 — 틱 아카이브 수집 전용, 매매 미채택)
- 신규 REST는 **TR(api_id)별 독립 제한** — 동일 TR 반복 시 **≈초당 1건(버스트 2)** 수렴, 서로 다른 TR은 합산 처리량이 더 높음. 초과 시 429 → 백오프.
- 공식 명시 수치가 불투명해 **보수적으로 초당 1**로 둔다(실측 후 상향 가능).
- 출처: [키움 REST API 래퍼(레이트리미터 동작)](https://github.com/younghwan91/kiwoom-rest-api), [키움 OpenAPI 개발가이드](https://openapi.kiwoom.com/)

## 통합 레이트리미터 (`common/rate_limit.py`)

토큰버킷 기반. 호출부는 요청 직전 `acquire(provider, group)` 한 줄만 추가하면 된다.

```python
from common import rate_limit

# Toss 캔들 호출 전
rate_limit.acquire("toss", "MARKET_DATA_CHART")
r = httpx.get(...)

# KIS: 모의/실전 한도가 다름(5 vs 20). rate는 버킷 최초 생성 시에만 반영(first-wins)되므로,
# 모드별로 group을 분리한다.
rate_limit.acquire("kis", "rest-real" if not KIS_MOCK else "rest-mock",
                   rate=20 if not KIS_MOCK else 5)
```

- **균등 페이싱(기본)**: `capacity` 기본값 1 → 버스트 없이 `rate`개/초로 고르게. 토큰버킷 `capacity=rate`면 고정 1초 윈도우 경계에서 최대 ~2×rate가 통과할 수 있어, 엄격 한도에선 순간 초과 위험 → 기본은 버스트 없음. 버스트가 필요하면 `TokenBucket(rate, capacity=...)`로 명시.
- **first-wins**: `(provider, group)` 버킷은 최초 생성 시 rate로 고정. 모드별 한도가 다르면 group을 나눈다(위 예).

배선 완료(`acquire` 호출 위치): `common/kis_account.py`·`common/kis_balance.py`·`common/kis_order.py`·`common/kis_overseas_price.py`(kis:rest), `batch/backtest/toss_intraday.py`(toss:MARKET_DATA_CHART), `common/notify_telegram.py`(telegram:send). 미배선(현재 호출처에 `acquire` 미적용): toss:AUTH(`toss_client`)·upbit:quotation(`upbit_daily`/`upbit_candles`)·kiwoom:tr(`stock_kiwoom`).

## "Kafka / Airflow로 하면 되나?" — 아니다

- **Kafka**: 분산 이벤트 로그/스트리밍(생산-소비 분리·내구성·리플레이)용. **아웃바운드 HTTP 호출을 초당 단위로 스로틀하는 도구가 아니다.** "요청을 큐에 넣고 컨슈머가 일정 속도로 소비"하게 억지로 만들 순 있으나, 단순 한도 제어엔 과한 인프라.
- **Airflow**: 워크플로 스케줄러(DAG·배치). pool/concurrency로 **태스크 동시성**은 막지만 **초 단위 호출 속도 제어**는 못 한다.
- **정답**: 클라이언트측 **토큰버킷**(이 모듈). 작고 검증된 패턴.
- **다중 프로세스/VM가 같은 자격증명을 공유**해 전역 한도가 필요해지면 → **Redis 백엔드 토큰버킷**(또는 단일 게이트웨이 프로세스). Kafka는 "이미 내구성 있는 요청 큐가 필요한" 경우에만.
- 현재는 일 1회 배치·단일 프로세스 백필이라 **인프로세스로 충분**.
