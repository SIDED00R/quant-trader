"""공용 고정 상수 (단일 책임: 중복·동기화-위험 상수의 단일 출처).

env로 바꿔야 하는 값은 여기가 아니라 common/config.py(런타임 설정)에 둔다.
여기에는 코드 전반에 **중복**되거나 함께 바뀌어야 하는 **고정** 값만 모은다.
단일 사용 상수(Kafka group id, 개별 전략 파라미터, 엔드포인트 URL)는 지역성을 위해 각 모듈에 남긴다.
"""

# ── ClickHouse 컬럼 리스트 (insert column_names 동기화 위험 → 단일 출처) ──
COLUMNS_TICKS = ["symbol", "price", "volume", "side", "trade_ts", "seq"]
COLUMNS_CANDLES = ["symbol", "window_start", "open", "high", "low", "close", "volume"]
COLUMNS_STOCK_CANDLES_1D = [
    "symbol", "window_start", "open", "high", "low", "close", "volume",
    "currency", "market",
]
COLUMNS_STOCK_CANDLES_1M = COLUMNS_STOCK_CANDLES_1D   # 주식 분봉도 동일 컬럼(통화/시장 차원 포함)

# ── HTTP 수집/재시도 (Upbit·Toss REST + WS 재연결 백오프 공통) ──
HTTP_PAGE = 200            # 캔들/시세 페이지당 최대 레코드
HTTP_MAX_RETRIES = 6       # 429/5xx/전송오류 재시도 횟수
HTTP_MAX_BACKOFF = 30.0    # 지수 백오프 상한(초) — REST 재시도·WS 재연결 공통
HTTP_TIMEOUT = 20.0        # 캔들/시세 수집 REST 타임아웃(초)
BROKER_TIMEOUT = 15.0      # 브로커 인증/조회 REST 타임아웃(초)

# ── SEC EDGAR 예절 (User-Agent 단일 출처 — US 외부데이터 수집기 공용: 펀더·13F·섹터·어닝·내부자·FINRA·팩터) ──
SEC_USER_AGENT = "coin-auto-trader research jh.lee@kornukopia-ai.com"
SEC_UA_HEADERS = {"User-Agent": SEC_USER_AGENT}   # httpx headers= 인자용(각 수집기 로컬 _UA 재정의 금지)

# ── KIS 잔고 TR·기본 코드 (불투명 식별자 → 의미 부여) ──
KIS_TR_DOMESTIC_BALANCE = "TTTC8434R"   # 국내 잔고(모의는 V접두로 토글)
KIS_TR_OVERSEAS_BALANCE = "TTTS3012R"   # 해외 잔고
KIS_DEFAULT_EXCHANGE = "NASD"           # 해외 기본 거래소(나스닥)
KIS_DEFAULT_CURRENCY = "USD"
