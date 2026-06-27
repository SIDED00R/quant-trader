"""환경 설정 로딩 (단일 책임: 설정)."""
import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")

SYMBOLS = [
    s.strip()
    for s in os.getenv("SYMBOLS", "KRW-BTC,KRW-ETH,KRW-XRP,KRW-SOL,KRW-DOGE").split(",")
    if s.strip()
]
# True 면 업비트 전체 KRW 마켓을 동적 구독(SYMBOLS 무시), False 면 위 정적 목록 사용.
SUBSCRIBE_ALL_KRW = os.getenv("SUBSCRIBE_ALL_KRW", "false").strip().lower() in ("1", "true", "yes")

TOPIC_TICKS = "market.ticks"

# ── ClickHouse ──
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
CLICKHOUSE_HTTP_PORT = int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "ch_pw")
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "coin_analytics")

# ── Kafka 토픽 ──
TOPIC_ORDERS = "orders"
TOPIC_EXECUTIONS = "executions"
TOPIC_SIGNALS = "strategy.signals"   # 전략 부하 → commander 신호 버스(4단계 라이브 배선)
TOPIC_STOCK_TICKS = "stock.ticks"    # 키움 실시간 주식체결(7단계 주식 확장)

# ── 키움증권 주식 (7단계: 주식 확장) ──
# 운용 유니버스는 8단계 백테스트 후 확정 — 아래는 잠정 데이터 수집 대상(6자리 종목코드).
STOCK_SYMBOLS = [
    s.strip()
    for s in os.getenv("STOCK_SYMBOLS", "005930,000660").split(",")
    if s.strip()
]
# 모의(mock) vs 실전(real) 도메인 선택. 모의계좌 검증 단계 → 기본 모의.
KIWOOM_MOCK = os.getenv("KIWOOM_MOCK", "true").strip().lower() in ("1", "true", "yes")
KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY", "")       # 키움 Open API appkey
KIWOOM_APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")  # 키움 Open API secretkey
KIWOOM_ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT_NO", "")  # 모의 계좌번호(단건주문용, 7단계 #5)
KIWOOM_REST_BASE = "https://mockapi.kiwoom.com" if KIWOOM_MOCK else "https://api.kiwoom.com"
KIWOOM_WS_URL = (
    "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
    if KIWOOM_MOCK
    else "wss://api.kiwoom.com:10000/api/dostk/websocket"
)

# ── 거래 ──
FEE_RATE = Decimal(os.getenv("FEE_RATE", "0.0005"))  # 0.05%
# 주식 매도 거래세(증권거래세+농특세). 2026 KOSPI/KOSDAQ 0.20%. 매수엔 없음·코인=0(국내주식만 적용).
STOCK_SELL_TAX_RATE = Decimal(os.getenv("STOCK_SELL_TAX_RATE", "0.0020"))

# ── 토스증권 Open API (데이터/조회 전용 — 매매는 키움 모의 유지) ──
# 주식 일봉 백필 데이터 소스(백테스트 입력). client_credentials OAuth2, 클라이언트당 토큰 1개.
TOSS_CLIENT_ID = os.getenv("TOSS_CLIENT_ID", "")
TOSS_CLIENT_SECRET = os.getenv("TOSS_CLIENT_SECRET", "")
TOSS_REST_BASE = "https://openapi.tossinvest.com"

# ── 한국투자증권 KIS (모의 체결 브로커 — KR+US 통합) ──
# 계좌 1개로 국내/해외 모의 체결. OAuth2 access_token(약 24h). 토큰 재발급 횟수 제한 있어 캐시 필수.
KIS_MOCK = os.getenv("KIS_MOCK", "true").strip().lower() in ("1", "true", "yes")
KIS_APPKEY = os.getenv("KIS_APPKEY", "")          # KIS Developers appkey
KIS_APPSECRET = os.getenv("KIS_APPSECRET", "")    # KIS Developers appsecret
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")  # 계좌번호 'CANO-PRDT'(앞 8자리-상품코드 2자리)
KIS_REST_BASE = (
    "https://openapivts.koreainvestment.com:29443"  # 모의
    if KIS_MOCK
    else "https://openapi.koreainvestment.com:9443"  # 실전
)

# ── 웹 대시보드 인증 (Basic Auth) ──
# WEB_PASSWORD 가 비어 있으면 인증 비활성(로컬 개발용). 운영(VM)에서는 반드시 설정.
WEB_USER = os.getenv("WEB_USER", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")

# ── 구글 OAuth / 세션 인증 ──
# GOOGLE_CLIENT_ID/SECRET 가 모두 설정되면 OAuth 인증 활성. 미설정 시 비활성(로컬 개발).
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
AUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
# 로그인 허용 이메일(allowlist). 신원확인 != 권한부여이므로 반드시 제한한다.
ALLOWED_EMAILS = {
    e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()
}
# 세션 쿠키 서명 키. 운영에서는 반드시 강한 무작위 값으로 설정(openssl rand -hex 32).
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-change-me")
# 공개 도메인(Caddy). OAuth redirect_uri 구성에 사용.
SITE_ADDRESS = os.getenv("SITE_ADDRESS", "")
OAUTH_REDIRECT_URI = (
    f"https://{SITE_ADDRESS}/auth/callback"
    if SITE_ADDRESS
    else "http://localhost:8000/auth/callback"
)
# 신규 계정 초기 가상자금(원)
INITIAL_BALANCE = Decimal(os.getenv("INITIAL_BALANCE", "10000000"))

# ── 자동매매 전략 (규율 기반 SMA) ──
SMA_SHORT = int(os.getenv("SMA_SHORT", "7"))             # 단기 이동평균(틱 수)
SMA_LONG = int(os.getenv("SMA_LONG", "25"))              # 장기 이동평균(틱 수)
# 매수 1회 금액 = 현재 현금 잔고 × (신호 강도에 비례한 비율). 약한 교차→MIN, 강한 교차→MAX.
STRATEGY_ORDER_FRACTION_MIN = Decimal(os.getenv("STRATEGY_ORDER_FRACTION_MIN", "0.05"))  # 약신호 매수 비율
STRATEGY_ORDER_FRACTION_MAX = Decimal(os.getenv("STRATEGY_ORDER_FRACTION_MAX", "0.20"))  # 강신호 매수 비율
STRATEGY_STRONG_GAP = Decimal(os.getenv("STRATEGY_STRONG_GAP", "0.015"))  # 이 이평선 간격(비율) 이상이면 MAX 비율
STRATEGY_COOLDOWN_SEC = int(os.getenv("STRATEGY_COOLDOWN_SEC", "3600"))   # (계정,종목) 재진입 쿨다운(초). 1분봉 기준 60봉=1h(과매매 차단). 라이브 틱봇은 .env로 축소
STRATEGY_STOP_LOSS_PCT = Decimal(os.getenv("STRATEGY_STOP_LOSS_PCT", "1.2"))       # 손절 %(평단 대비)
STRATEGY_TAKE_PROFIT_PCT = Decimal(os.getenv("STRATEGY_TAKE_PROFIT_PCT", "2.0"))   # 익절 %
STRATEGY_TRAIL_ARM_PCT = Decimal(os.getenv("STRATEGY_TRAIL_ARM_PCT", "0.8"))       # 트레일링 무장 임계 %
STRATEGY_TRAIL_GIVEBACK_PCT = Decimal(os.getenv("STRATEGY_TRAIL_GIVEBACK_PCT", "0.5"))  # 고점 대비 되돌림 %
STRATEGY_ENTRY_BAND = Decimal(os.getenv("STRATEGY_ENTRY_BAND", "0.0015"))  # SMA 이격 밴드(비율)
STRATEGY_CONFIRM_TICKS = int(os.getenv("STRATEGY_CONFIRM_TICKS", "2"))     # 확인봉 틱 수
STRATEGY_MIN_HOLD_SEC = int(os.getenv("STRATEGY_MIN_HOLD_SEC", "1800"))    # 데드크로스 청산 최소보유(초). 1분봉 기준 30봉
STRATEGY_WARMUP_SEC = int(os.getenv("STRATEGY_WARMUP_SEC", "1500"))        # 기동 후 신규 진입/데드크로스 청산 보류(초). 1분봉 기준 25봉(=SMA_LONG)
STRATEGY_MAX_POSITIONS = int(os.getenv("STRATEGY_MAX_POSITIONS", "10"))    # 계정당 동시 보유 종목 수 상한(현금 소진·과분산 방지)
# 수수료 인지 필터(1.5단계): 진입 신호 강도(이평선 간격)가 이 비율 미만이면 진입 차단 — 약신호 과매매·수수료 출혈을 막는다.
# 왕복 수수료(2×FEE_RATE)+슬리피지보다 충분히 큰 값으로 둔다(기본 0.5% ≫ 왕복 0.1%).
STRATEGY_MIN_EDGE_PCT = Decimal(os.getenv("STRATEGY_MIN_EDGE_PCT", "0.005"))
# 데드크로스 청산 사용 여부(1.5단계). baseline에서 데드크로스가 최대 출혈원(−22.5M)이라 기본 비활성(청산은 STOP/TAKE/TRAIL만).
STRATEGY_DEADCROSS_EXIT = os.getenv("STRATEGY_DEADCROSS_EXIT", "false").strip().lower() in ("1", "true", "yes")

# 업비트 최소 주문 금액(이 금액 미만 매수는 스킵). 정본 위치는 여기(config) — sma_trader의 동명 상수는 통합 대기(코드리뷰 D1/D2 deferred).
MIN_ORDER_KRW = Decimal(os.getenv("MIN_ORDER_KRW", "5000"))

# ── 저회전 추세추종 전략 (3단계, strategy/trend.py) ──
# 일봉(상위 타임프레임) 기준 long-or-cash. 가드는 초(秒)가 아닌 **봉 수**로 둔다(일봉=1봉/일).
TREND_SHORT = int(os.getenv("TREND_SHORT", "10"))            # 단기 SMA(봉)
TREND_LONG = int(os.getenv("TREND_LONG", "40"))             # 장기 SMA(봉). 단기>장기=상승추세→보유
TREND_ENTRY_BAND = Decimal(os.getenv("TREND_ENTRY_BAND", "0.0"))  # 히스테리시스 마진(이격 비율). 진입은 +band 초과, 청산은 -band 미만 — whipsaw·churn 차단
TREND_VOL_TARGET = Decimal(os.getenv("TREND_VOL_TARGET", "0.5"))  # 목표 연율 변동성. 진입 비중 = min(MAX, VOL_TARGET/실현변동성)
TREND_VOL_LOOKBACK = int(os.getenv("TREND_VOL_LOOKBACK", "20"))   # 실현변동성 산출 봉 수(일별 수익률 표준편차)
TREND_MAX_WEIGHT = Decimal(os.getenv("TREND_MAX_WEIGHT", "1.0"))  # 1회 비중 상한(현물=1.0, 레버리지 불가)
TREND_REGIME_MAX_VOL = Decimal(os.getenv("TREND_REGIME_MAX_VOL", "2.0"))  # 연율 실현변동성이 이 값 초과면 추세 무관 강제 현금(극단 레짐 필터)
TREND_BARS_PER_YEAR = int(os.getenv("TREND_BARS_PER_YEAR", "365"))  # 변동성 연율화 계수(일봉 24/7=365). 변경 시 타임프레임과 일치시킬 것
# 보유 중 변동성 타게팅 리밸런싱 밴드(상대): |현재비중-목표비중|/목표 > 밴드일 때만 재조정. 0=비활성(진입시 사이징만, 저회전 유지).
TREND_REBALANCE_BAND = Decimal(os.getenv("TREND_REBALANCE_BAND", "0.0"))
# 앙상블(다중 추세속도) 합성 목표비중 재조정 밴드. 채택값 0.5(BTC/ETH 6.6년 교차검증: Sharpe·일관성·저회전 최적).
ENSEMBLE_REBALANCE_BAND = Decimal(os.getenv("ENSEMBLE_REBALANCE_BAND", "0.5"))
# 라이브 앙상블 운용 유니버스(채택안 = BTC/ETH). 라이브 신호 워커가 이 종목만 일봉 신호 산출.
ENSEMBLE_SYMBOLS = [s.strip() for s in os.getenv("ENSEMBLE_SYMBOLS", "KRW-BTC,KRW-ETH").split(",") if s.strip()]
# 적응형 가중치 사용 여부(5단계). False=동일가중(현 동작 보존). True=strategy_weights 테이블 값 사용.
# ⚠️ 적응 가중치는 과적합 위험(walk-forward에서 고정>최적화). 검증 전까지 기본 off.
ENSEMBLE_ADAPTIVE = os.getenv("ENSEMBLE_ADAPTIVE", "false").strip().lower() in ("1", "true", "yes")

# ── 횡단면(인트라데이) 전략 (strategy/cross_sectional.py) ──
# 매 봉 전 종목을 랭킹해 상위 N을 동일가중 long-or-cash로 보유(research §2.4 롱 다리). 회전율 억제가 1차 생존조건.
XS_LOOKBACK = int(os.getenv("XS_LOOKBACK", "30"))            # 랭킹 수익률 산출 룩백(봉)
XS_TOP_N = int(os.getenv("XS_TOP_N", "10"))                 # 보유 상위 종목 수
XS_REBALANCE_BAND = Decimal(os.getenv("XS_REBALANCE_BAND", "0.3"))  # 목표 드리프트 밴드(저회전 — decide와 공유)
XS_MAX_WEIGHT = Decimal(os.getenv("XS_MAX_WEIGHT", "0.2"))  # 종목당 비중 상한(동일가중 1/N과 min)

# ── 인트라데이 세션 전략 (strategy/intraday.py) ──
# 세션(거래일) 기준 단일종목 long-or-cash, 오버나잇 미보유(마감 봉 청산). 봉=분봉 가정.
ORB_OPENING_BARS = int(os.getenv("ORB_OPENING_BARS", "30"))      # 개장 레인지 산정 봉 수(예 30분)
MOM_SIGNAL_BARS = int(os.getenv("MOM_SIGNAL_BARS", "30"))        # 인트라데이 모멘텀 신호 산정 봉 수
MOM_THRESHOLD = Decimal(os.getenv("MOM_THRESHOLD", "0.0"))       # 개장 N봉 수익률 임계(초과 시 매수)

# ── 부하 재평가 잡 가중치 정책(5.4, backtest/reeval_weights.py) ──
# 보수적 가드: "최적화로 향상"이 아니라 "열화 부하 자동 강등"이 목적. 동일가중 기준 소폭 이탈만 허용.
# 각 가중치 하한/상한 = 동일가중 × 배수(부하 수 무관). floor>0 → demote≠delete(완전 제거 금지).
ENSEMBLE_WEIGHT_FLOOR_MULT = float(os.getenv("ENSEMBLE_WEIGHT_FLOOR_MULT", "0.5"))  # 동일가중의 50% 이상 보장
ENSEMBLE_WEIGHT_CAP_MULT = float(os.getenv("ENSEMBLE_WEIGHT_CAP_MULT", "1.5"))      # 동일가중의 150% 이하(독점 차단)
# DSR(=고정구성이라 PSR) 게이트: 부하의 OOS 엣지 유의확률이 이 값 미만이면 강등(floor로). 3단계 성공기준과 동일.
ENSEMBLE_DSR_GATE = float(os.getenv("ENSEMBLE_DSR_GATE", "0.90"))
# EWMA 평활: 신규 타깃 반영 비율(작을수록 느린 갱신 → 급변·과적합 추격 방지).
ENSEMBLE_WEIGHT_EWMA = float(os.getenv("ENSEMBLE_WEIGHT_EWMA", "0.2"))

# ── PostgreSQL ──
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "trader")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "trader_pw")
POSTGRES_DB = os.getenv("POSTGRES_DB", "coin_trading")
POSTGRES_DSN = (
    f"host={POSTGRES_HOST} port={POSTGRES_PORT} user={POSTGRES_USER} "
    f"password={POSTGRES_PASSWORD} dbname={POSTGRES_DB}"
)
