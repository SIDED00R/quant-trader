# 키움증권 신규 REST API — 주식(STOCK) 확장 토대

> 7단계(주식 토대)의 단일 출처. 기존 코인 인프라(`docs/model.md`·`docs/baseline.md`)와 동일한 톤·검증 원칙(검증된 사실만 기재, 불확실은 "확인필요"로 명시)을 따른다. 모든 API 사실에는 출처 URL을 인라인으로 단다.

## 1. 개요 — 왜 키움, 왜 신규 REST API

코인 파이프라인(업비트 WebSocket → Kafka `market.ticks` → ClickHouse → 앙상블 모의매매)을 **주식으로 확장**한다. 한국 주식 모의투자를 코드로 자동화할 수 있는 현실적 선택지는 키움증권이다.

- **레거시 OpenAPI+(OCX)** 는 Windows COM/OCX 의존이라 Docker/Linux VM에서 못 돈다(현 인프라는 GCE Linux 2-VM).
- **신규 REST API**(`openapi.kiwoom.com`)는 표준 HTTPS REST + WebSocket이라 **OS 비의존(Win/Mac/Linux)** 이며, 207개 국내주식 엔드포인트·19종 실시간을 제공한다. ([openapi.kiwoom.com](https://openapi.kiwoom.com/))
- 인증·요청 구조가 업비트/구글 OAuth와 닮아 있어 **기존 `common/` 클라이언트·ingester·sink 패턴을 1:1 미러링**할 수 있다.

> 목표는 7단계 한정 — **단건 주문 왕복 검증**(주문 → 모의체결 → 대시보드 반영)까지. 앙상블·자동매매·통합 대시보드는 8·9단계.

## 2. 인증·도메인

### 베이스 URL (실전 vs 모의)
| 환경 | REST 베이스 | WebSocket |
|---|---|---|
| 실전(real) | `https://api.kiwoom.com` | `wss://api.kiwoom.com:10000` |
| 모의(mock) | `https://mockapi.kiwoom.com` | `wss://mockapi.kiwoom.com:10000` |

코드·헤더·바디 구조는 동일하고 **도메인만 교체**해 실전↔모의 전환한다. 모의는 **KRX(코스피/코스닥)만 지원**한다. 포트 10000은 실시간 WebSocket 전용(REST는 443). ([openapi.kiwoom.com/m/guide/apiguide](https://openapi.kiwoom.com/m/guide/apiguide))

### OAuth2 접근토큰 발급 (api-id `au10001`)
- **요청**: `POST {도메인}/oauth2/token`, 헤더 `Content-Type: application/json;charset=UTF-8`, 바디 `{"grant_type":"client_credentials","appkey":"...","secretkey":"..."}`
- **응답**: `token`(접근토큰 문자열), `token_type`("bearer"), `expires_dt`(만료시각 `YYYYMMDDhhmmss`), `return_code`(0=성공), `return_msg`
- **폐기**: api-id `au10002`(엔드포인트 경로 문자열은 확인필요)
- 토큰 유효기간은 약 24시간 — 만료 전 재발급 로직 필요.

> ⚠️ **필드명 주의**: 키움은 `secretkey`/`token`을 쓴다. 한국투자증권(KIS)의 `appsecret`/`access_token`과 **혼동 금지**. ([openapi.kiwoom.com/guide/apiguide](https://openapi.kiwoom.com/guide/apiguide), [pabburi 토큰발급 튜토리얼](https://www.pabburi.co.kr/content/php/%ED%82%A4%EC%9B%80%EC%A6%9D%EA%B6%8C-rest-api-%EC%A0%91%EA%B7%BC%ED%86%A0%ED%81%B0-%EB%B0%9C%EA%B8%89/))

### 공통 요청 헤더 규약
공식 가이드의 공통 Request Header 표 기준 ([openapi.kiwoom.com/m/guide/apiguide?jobTpCode=05](https://openapi.kiwoom.com/m/guide/apiguide?jobTpCode=05)):

| 헤더 | 필수 | 설명 |
|---|---|---|
| `authorization` | **Y** | `Bearer {token}` 형식(토큰타입 "Bearer" 접두) |
| `api-id` | **Y** | 7자리 TR코드(예 `kt10000`) |
| `Content-Type` | (POST) | `application/json;charset=UTF-8` |
| `cont-yn` | N | 연속조회 여부. 응답 헤더값을 다음 요청에 세팅 |
| `next-key` | N | 연속조회 키. 응답 헤더값을 다음 요청에 세팅 |

- **정정(적대적 검증 우선)**: `cont-yn`/`next-key`는 **비필수(N)** 이며 페이지네이션용이다. 주문(`kt10000`/`kt10001`)은 연속조회 대상이 아니므로 생략하거나 빈 값으로 둔다. 주문에서 실제 필수는 `authorization`+`api-id`(+POST의 `Content-Type`)뿐.
- 연속조회 키는 **HTTP 헤더**(`cont-yn`/`next-key`)로 주고받는다(일부 래퍼의 바디 `cont_yn`/`next_key`는 추상화일 뿐, 실제 전송은 헤더). 응답 헤더에 `api-id`/`cont-yn`/`next-key`가 echo된다. ([openapi.kiwoom.com/m/guide/apiguide?jobTpCode=15](https://openapi.kiwoom.com/m/guide/apiguide?jobTpCode=15))

### 호출 유량 (rate limit)
- **확인필요(medium)**: 오픈소스 래퍼 기준 TR당 약 1 req/s·버스트 2, 초과 시 HTTP 429 + `return_code:5`("허용된 요청 개수를 초과하였습니다"). 모의는 .NET 래퍼 기준 20건/초·동시구독 40종목·WS 1연결로 보고됨 — 공식 고지 수치와 다를 수 있으므로 token-bucket 리미터 + 자동 재시도를 보수적으로 적용. ([younghwan91/kiwoom-rest-api](https://github.com/younghwan91/kiwoom-rest-api), [dongbin300/KiwoomRestApi.Net](https://github.com/dongbin300/KiwoomRestApi.Net))

## 3. 실시간 시세 WebSocket

전체 URL: `wss://api.kiwoom.com:10000/api/dostk/websocket`(실전) · `wss://mockapi.kiwoom.com:10000/api/dostk/websocket`(모의). 핸드셰이크에는 토큰 헤더가 없고 **연결 후 LOGIN 메시지**로 인증한다. ([breadum.github.io/kiwoom-restful](https://breadum.github.io/kiwoom-restful/latest/api), [pabburi 실시간시세 튜토리얼](https://www.pabburi.co.kr/content/php/%ED%82%A4%EC%9B%80%EC%A6%9D%EA%B6%8C-rest-api-%EC%8B%A4%EC%8B%9C%EA%B0%84%EC%8B%9C%EC%84%B8%EC%A1%B0%ED%9A%8C-%EB%B0%8F-%EC%A1%B0%EA%B1%B4%EA%B2%80%EC%83%89/))

### 흐름
1. **LOGIN**: `{"trnm":"LOGIN","token":"<accessToken>"}` 전송 → 서버가 `return_code`(0=성공)/`return_msg` 회신.
2. **등록(REG)**: `{"trnm":"REG","grp_no":"1","refresh":"1","data":[{"item":["005930","000660"],"type":["0B"]}]}`
   - `refresh="1"`=기존 등록 유지(추가), `"0"`=초기화 후 신규(확인필요 — 2차 자료 일관, 공식 원문 0/1 정의 미확인).
   - 종목코드: KRX는 6자리 숫자만(`005930`), NXT 시장은 `_NX` 접미사(`123456_NX`). 모의는 KRX만.
3. **해지(REMOVE)**: 같은 구조에 `trnm`만 `"REMOVE"`로. (그룹 전체 해제 vs item/type 명시 필요 여부는 자료가 엇갈림 — 확인필요)
4. **수신(REAL)**: `{"trnm":"REAL","data":[{"type":"0B","name":"주식체결","item":"005930","values":{"10":"...","20":"..."}}]}`
   - `values`는 **FID(필드코드)→문자열값** 맵. 부호 포함 문자열(예 `'-1200'`)이라 클라이언트에서 부호·스케일 파싱 필요.
5. **PING/PONG**: 서버가 `{"trnm":"PING",...}`를 보내면 **받은 그대로 echo** 회신해 keepalive 유지(주기·타임아웃 수치는 확인필요).

### 주요 실시간 type / FID
- type: `0B`=주식체결(현재가/체결), `0D`=주식호가잔량, `0C`=우선호가, `00`=주문체결, `04`=잔고 (총 19종). ([pypi kiwoom-restful](https://pypi.org/project/kiwoom-restful/), [bamjun/kiwoom-rest-api](https://github.com/bamjun/kiwoom-rest-api))
- `0B` 대표 FID: `10`=현재가, `11`=전일대비, `12`=등락율, `13`=누적거래량, `15`=체결량, `20`=체결시간.
- **그룹당 최대 100종목**(확인필요 — 계정/세션 합산 한도인지 그룹별인지 불명확). 초과 시 REMOVE→REG 로테이션 필요.

> **seq/단조성 주의**: ClickHouse `stock_ticks`의 `ORDER BY (symbol, seq)` 중복제거 전제는 종목별 단조증가 식별자다. 키움 실시간에는 업비트 `sequential_id` 같은 명시 시퀀스가 없을 수 있어 **체결시간(FID 20) + 수신 카운터** 등으로 seq를 합성해야 한다(설계 결정 필요).

## 4. REST 엔드포인트 요약 (api-id 포함)

| 용도 | 메서드 · 경로 | api-id | 비고 |
|---|---|---|---|
| 토큰 발급 | POST `/oauth2/token` | `au10001` | client_credentials |
| 토큰 폐기 | POST `/oauth2/...`(경로 확인필요) | `au10002` | |
| 주식기본정보(현재가) | POST `/api/dostk/stkinfo` | `ka10001` | 바디 `{"stk_cd":"005930"}`, 현재가 `cur_prc`(필드명 medium) |
| 주식호가 | POST `/api/dostk/mrkcond` | `ka10004` | 시세 그룹 |
| 매수주문 | POST `/api/dostk/ordr` | `kt10000` | |
| 매도주문 | POST `/api/dostk/ordr` | `kt10001` | 정정 `kt10002`·취소 `kt10003` |
| 예수금상세현황 | POST `/api/dostk/acnt` | `kt00001` | 현금 잔고 |
| 계좌평가현황 | POST `/api/dostk/acnt` | `kt00004` | 보유종목 평가(필드명 확인필요) |
| 체결잔고 | POST `/api/dostk/acnt` | `kt00005` | |

**주문 바디 필드** ([pabburi 주식주문 튜토리얼](https://www.pabburi.co.kr/content/php/%ED%82%A4%EC%9B%80%EC%A6%9D%EA%B6%8C-rest-api-%EC%A3%BC%EC%8B%9D%EC%A3%BC%EB%AC%B8%ED%95%98%EA%B8%B0/)):
- `dmst_stex_tp`(거래소구분 KRX/NXT/SOR), `stk_cd`(종목코드), `ord_qty`(수량, **정수**), `ord_uv`(주문단가), `trde_tp`(매매구분 `0`=보통/지정가, `3`=시장가).
- **응답**: `ord_no`(주문번호), `return_code`(0=성공), `return_msg`.
- `stkinfo`(종목정보)와 `mrkcond`(시세/호가)는 path 그룹이 다르다(`ka10001`=stkinfo, `ka10004`=mrkcond). `acnt` 하위 api-id 1:1 매핑과 응답 필드명은 서드파티 래퍼 기준이라 **공식 가이드 재대조 필요**. ([younghwan91/kiwoom-rest-api](https://github.com/younghwan91/kiwoom-rest-api))

## 5. 모의투자 계정 발급·인증 흐름 (단계별)

1. **포털 신청(PC 전용)**: `openapi.kiwoom.com` 로그인 → 'API 사용신청' → `appkey`/`secretkey` 발급 + **IP 등록** + **계좌(HTS ID) 연결**. 모바일 신청 불가. ([openapi.kiwoom.com](https://openapi.kiwoom.com/))
2. **모의 등록**: 신규 REST API는 포털 사용등록만으로 모의 이용 가능(레거시 OCX의 '상시모의투자 참가신청'은 REST에 불필요). 단 **모의 계좌번호가 자동발급인지 별도 발급 절차인지는 확인필요**.
3. **토큰 발급**: `POST mockapi.kiwoom.com/oauth2/token`(`au10001`)로 Bearer 토큰 수령.
4. **단건 매수**: `POST mockapi.kiwoom.com/api/dostk/ordr`(`kt10000`, **1주** 매수) → `ord_no` 수령.
5. **모의체결 대기**: 모의체결은 **정규장 시간(09:00~15:30 KST)** 에 실제 시세 기반으로 진행 — 장외 테스트 시 미체결(주문접수만)이 정상.
6. **체결/잔고 확인**: `POST mockapi.kiwoom.com/api/dostk/acnt`(`kt00004`/`kt00005`)로 보유수량·체결 확인.

> 동일 Bearer 토큰·api-id 헤더를 쓰고 도메인만 `mockapi.kiwoom.com`으로 고정한다. 키는 실전·모의 공통. ([dongbin300/KiwoomRestApi.Net](https://github.com/dongbin300/KiwoomRestApi.Net))

## 6. 한국 시장 메커니즘 (백테스트·체결 모델용, 2026 현행)

### 호가단위(tick size) — KOSPI·KOSDAQ 동일, 2023-01-25 시행
| 가격대(원) | 호가단위(원) |
|---|---|
| ~1,000 미만 | 1 |
| 1,000~2,000 | 1 |
| 2,000~5,000 | 5 |
| 5,000~10,000 | 10 |
| 10,000~20,000 | 10 |
| 20,000~50,000 | 50 |
| 50,000~100,000 | 100 |
| 100,000~200,000 | 100 |
| 200,000~500,000 | 500 |
| 500,000 이상 | 1,000 |

경계는 '미만/이상'(정확히 50,000원이면 5만~10만 구간=100원). ([삼성증권 공지](https://samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=19236), [Business Post](https://www.businesspost.co.kr/BP?command=article_view&num=303760))

### 운영시간 (KOSPI·KOSDAQ 동일)
- 정규장 **09:00~15:30**. 장 시작 동시호가 08:30~09:00(09:00 시가 결정), 장 마감 동시호가 15:20~15:30(15:30 종가). 시간외 종가 15:40~16:00, 시간외 단일가 16:00~18:00(10분 단위, 당일 종가 ±10%). ([법제처 생활법령](https://easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=1701&ccfNo=2&cciNo=1&cnpClsNo=2))

### 가격제한폭
- 전일 종가 대비 **±30%**(2015-06-15 ±15%→±30%). 호가단위로 내림/올림해 상·하한가 산정. ([나무위키 가격제한폭](https://namu.wiki/w/%EA%B0%80%EA%B2%A9%EC%A0%9C%ED%95%9C%ED%8F%AD))

### 거래 비용
- **위탁수수료(키움 영웅문 온라인)**: 약 0.015%. 유관기관 제비용 포함/별도 표기가 자료마다 달라 백테스트는 **보수적으로 왕복 0.03~0.035%** 가정. ([stockstalker 키움수수료](https://stockstalker.co.kr/kiwoom-fee/))
- **증권거래세(매도만, 농특세 포함)**:
  - **2025년**: KOSPI 0%+농특세 0.15%=**0.15%**, KOSDAQ 0.15%=**0.15%**.
  - **2026년(2026-01-01 양도분~)**: KOSPI 본세 0.05%+농특세 0.15%=**0.20%**, KOSDAQ 본세 0.20%=**0.20%**, KONEX 0.10%, K-OTC 0.20%. 농특세(0.15%)는 KOSPI에만 부과. ([증권거래세법 시행령 제5조](https://www.law.go.kr/lsLinkCommonInfo.do?lspttninfSeq=64014&chrClsCd=010202), [한국세정신문](https://taxtimes.co.kr/news/article.html?no=272624))
- **비대칭**: 세금은 매도에만, 수수료는 양방향. **2026 KOSPI/KOSDAQ 왕복 ≈ 0.015%+0.015%+0.20% ≈ 0.23%**(손실 거래에도 매도세 부과, 손익 무관).
- **현 코드 대비**: 코인 `FEE_RATE=0.0005`(매수·매도 대칭, 세금 0). 주식은 매도세 비대칭이 추가됨.
- **모의 환경 수수료/세금 차감 여부는 확인필요** — 실전과 정합시키려면 실전 비용 동일 적용이 안전.

### 주문 단위
- 정규장은 **1주(정수)** 단위. 소수점(소수단위) 매매는 장마감 기준가 취합 체결의 별도 체계이므로 정규장 모델에서는 **정수 수량으로 제한**한다. ([나무위키 소수점주식](https://namu.wiki/w/%EC%86%8C%EC%88%98%EC%A0%90%20%EC%A3%BC%EC%8B%9D))

### 종목코드
- 6자리 숫자(보통주 끝자리 0, 우선주 5 등). 시장구분은 코드만으로 단정 말고 거래소 메타 사용. **앞자리 0 손실 방지 위해 6자리 0-패딩 문자열로 저장**.

## 7. 기존 코인 인프라 재사용 매핑

| 코인(기존) | 주식(신규) | 패턴 |
|---|---|---|
| `ingester/upbit_ws.py` → `market.ticks` | `ingester/stock_kiwoom.py` → `stock.ticks` | WS 연결/구독/정규화/발행/지수백오프 미러. 단 LOGIN·토큰·PING echo 추가 |
| `common/schemas.Tick` | `common/schemas.StockTick`(또는 Tick 재사용) | `_dumps()`(asdict+default=str) 재사용. Decimal 가격/수량+side+trade_ts(UTC ISO)+seq |
| `common/upbit_markets.py`·`symbols.py` | `common/kiwoom_markets.py`·`stock_symbols.py` | TTL캐시+실패백오프+정적폴백 1:1 미러 |
| `common/kafka_client.py` | (그대로) | producer/consumer 팩토리 변경 불필요 |
| `sink/tick_clickhouse.py` → `ticks` | `sink/stock_tick_clickhouse.py` → `stock_ticks` | parse_row/배치(500/2s)/insert/수동commit 미러, 별도 GROUP_ID |
| `TOPIC_TICKS="market.ticks"` | `TOPIC_STOCK_TICKS="stock.ticks"` (config.py:19 근처) | kafka-init에 `--partitions 6 --config retention.ms=86400000` 라인 추가(AUTO_CREATE=false) |
| ClickHouse `ticks` | `stock_ticks`(ReplacingMergeTree, ORDER BY (symbol,seq)) | `db/clickhouse_schema.sql`에 DDL 추가(schema_loader 자동 적용) |
| Postgres `orders`/`executions`/`positions` | `ADD COLUMN IF NOT EXISTS asset_class TEXT`(+`broker_order_id TEXT`) | 단건 검증엔 컬럼 추가(A안)가 변경 최소. 별 테이블(B안)은 8·9단계 |
| `engine/matching.py`(Kafka 모의체결) | **우회** — 키움이 외부 체결을 돌려줌 | 주식 주문은 `order_outbox`/relay/engine 경로를 타면 안 됨(코인 엔진이 오소비) |
| `common/order_writer.place_order` | `common/kiwoom_client.py` + 별도 체결 기록 함수 | 키움 REST 주문 호출→`ord_no`→executions 직접 기록(uuid5 멱등 유지) |
| `api/routes/orders.py` | `api/routes/stock_orders.py`(신규) | orders.py 검증 미러 + 정수수량·장시간·세금 가드. main.py에 include_router 1줄 |
| `api/web/index.html` 체결 패널 | 주식 체결 패널 1개 + `loadStockExecs()` | refresh() Promise.all에 추가. 평가자산 합산은 자산군 분리 필요(통합은 9단계) |

### 체결·계좌 모델 확장점 — ✅ 백테스트 반영 완료(#120/PR#121), 라이브 가드는 #5 이월
- **정수 주문단위**: ✅ 백테스트 `backtest/engine.py`의 `_adjust_qty`(주식 `to_integral_value(ROUND_DOWN)`, <1주 skip 로그; 코인 무영향). 라이브 `place_order`/`stock_orders` 진입부 절사는 단건 주문 검증(#5) 때 적용.
- **매도 거래세**: ✅ `backtest/fills.py:tax(symbol,price,qty)`(**국내주식만** 0.20%, 미국·코인=0), `account.apply_sell(tax=...)`의 `proceeds=price*qty-fee-tax`, `models.ClosedTrade.sell_tax`. `STOCK_SELL_TAX_RATE` config 추가. 라이브는 키움/KIS 응답의 수수료/세금을 그대로 기록.
- **장 운영시간 게이트**: ✅ 신규 `common/market_hours.py`(`asset_class`·`is_coin`·`is_stock`·`is_market_open(symbol, now=None)` — 코인 항상 True, 국내주식 KRX 평일 09:00–15:30 KST, 미국주식 미지원=False). 휴장일 캘린더·라이브 주문 게이트는 #5.
- **라이브-백테스트 수학 미러링**(account.py 도크스트링 계약): ✅ 백테스트(세금·정수화) 반영, 라이브 미러링은 #5.

## 8. 종목 유니버스 선정 논의

코인이 6.6년 교차검증으로 BTC/ETH로 좁힌 것과 같은 철학 — **소수 우량·고유동성**으로 시작한다(과매매·수수료 출혈이 코인 baseline의 주범이었던 교훈).

### 후보 & 기준
- **기준**: (1) 최고 유동성(체결 슬리피지·미체결 최소), (2) 장기 일봉 표본 충분(추세 앙상블 검증에 필요한 T 확보), (3) 모의 KRX 지원, (4) 섹터 대표성, (5) 운영 단순성(WS 100종목 한도·rate limit 여유).
- **후보군**:
  - **A) 단일 대형주(삼성전자 005930)** — 최고 유동성·최장 표본. 코인 'BTC만'에 대응. 가장 단순하나 단일 종목 추세는 표본 길이 민감(코인 교훈).
  - **B) 대형주 2종목(삼성전자 005930 + SK하이닉스 000660)** — 'BTC/ETH'와 직결되는 대응. 반도체 편중이 약점.
  - **C) 지수 ETF 2종목(KODEX 200 069500 + KODEX 코스닥150 229200)** — 분산·낮은 개별리스크, 추세추종에 적합. 단 ETF는 거래세 면제/상이로 비용모델이 달라짐(확인필요).
  - **D) 대형주 5종목** — 코인 초기 5종목에 대응하나 과분산·과매매 위험(코인에서 폐기한 방향).

### 추천
- **B) 삼성전자(005930) + SK하이닉스(000660)** — 코인 BTC/ETH 철학과 1:1. 최고 유동성·최장 일봉 표본으로 동일 추세 앙상블(5/40·10/60·20/100) 재검증이 가능하고, 단건 왕복 검증에도 1주 단가가 합리적. 반도체 편중은 8단계 백테스트에서 섹터 분산(C안 ETF 혼합)으로 보강.
- 7단계(단건 검증)에서는 우선 **삼성전자 1종목 1주**로 주문 왕복만 확인하고, 유니버스 확정은 8단계 백테스트 성과로 결정.

## 9. 출처 목록

- 공식 가이드: [openapi.kiwoom.com/guide/apiguide](https://openapi.kiwoom.com/guide/apiguide) · [모바일 가이드](https://openapi.kiwoom.com/m/guide/apiguide) · [포털](https://openapi.kiwoom.com/)
- 토큰/주문/시세 튜토리얼(공식 인용): [pabburi 토큰](https://www.pabburi.co.kr/content/php/%ED%82%A4%EC%9B%80%EC%A6%9D%EA%B6%8C-rest-api-%EC%A0%91%EA%B7%BC%ED%86%A0%ED%81%B0-%EB%B0%9C%EA%B8%89/) · [pabburi 주문](https://www.pabburi.co.kr/content/php/%ED%82%A4%EC%9B%80%EC%A6%9D%EA%B6%8C-rest-api-%EC%A3%BC%EC%8B%9D%EC%A3%BC%EB%AC%B8%ED%95%98%EA%B8%B0/) · [pabburi 실시간](https://www.pabburi.co.kr/content/php/%ED%82%A4%EC%9B%80%EC%A6%9D%EA%B6%8C-rest-api-%EC%8B%A4%EC%8B%9C%EA%B0%84%EC%8B%9C%EC%84%B8%EC%A1%B0%ED%9A%8C-%EB%B0%8F-%EC%A1%B0%EA%B1%B4%EA%B2%80%EC%83%89/)
- 래퍼(교차검증): [younghwan91](https://github.com/younghwan91/kiwoom-rest-api) · [bamjun](https://github.com/bamjun/kiwoom-rest-api) · [breadum/kiwoom-restful](https://breadum.github.io/kiwoom-restful/latest/api) · [pypi kiwoom-restful](https://pypi.org/project/kiwoom-restful/) · [dongbin300 .NET](https://github.com/dongbin300/KiwoomRestApi.Net)
- 시장 메커니즘: [삼성증권 호가단위](https://samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=19236) · [법제처 거래시간](https://easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=1701&ccfNo=2&cciNo=1&cnpClsNo=2) · [증권거래세법 시행령](https://www.law.go.kr/lsLinkCommonInfo.do?lspttninfSeq=64014&chrClsCd=010202) · [한국세정신문 거래세](https://taxtimes.co.kr/news/article.html?no=272624) · [나무위키 가격제한폭](https://namu.wiki/w/%EA%B0%80%EA%B2%A9%EC%A0%9C%ED%95%9C%ED%8F%AD)

> **검증 한계**: 공식 가이드가 JS SPA라 일부 TR 상세 응답 필드·정확한 rate limit·refresh 0/1 정의·모의 계좌 발급 절차는 1차 확인을 못 해 "확인필요"로 표기했다. 구현 착수 전 PC 브라우저 로그인 후 공식 가이드 원문으로 해당 항목을 대조한다.
