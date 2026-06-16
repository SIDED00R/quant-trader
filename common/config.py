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

# ── 거래 ──
FEE_RATE = Decimal(os.getenv("FEE_RATE", "0.0005"))  # 0.05%

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
STRATEGY_COOLDOWN_SEC = int(os.getenv("STRATEGY_COOLDOWN_SEC", "15"))     # (계정,종목) 재진입 쿨다운(초)
STRATEGY_STOP_LOSS_PCT = Decimal(os.getenv("STRATEGY_STOP_LOSS_PCT", "1.2"))       # 손절 %(평단 대비)
STRATEGY_TAKE_PROFIT_PCT = Decimal(os.getenv("STRATEGY_TAKE_PROFIT_PCT", "2.0"))   # 익절 %
STRATEGY_TRAIL_ARM_PCT = Decimal(os.getenv("STRATEGY_TRAIL_ARM_PCT", "0.8"))       # 트레일링 무장 임계 %
STRATEGY_TRAIL_GIVEBACK_PCT = Decimal(os.getenv("STRATEGY_TRAIL_GIVEBACK_PCT", "0.5"))  # 고점 대비 되돌림 %
STRATEGY_ENTRY_BAND = Decimal(os.getenv("STRATEGY_ENTRY_BAND", "0.0015"))  # SMA 이격 밴드(비율)
STRATEGY_CONFIRM_TICKS = int(os.getenv("STRATEGY_CONFIRM_TICKS", "2"))     # 확인봉 틱 수
STRATEGY_MIN_HOLD_SEC = int(os.getenv("STRATEGY_MIN_HOLD_SEC", "20"))      # 데드크로스 청산 최소보유(초)
STRATEGY_WARMUP_SEC = int(os.getenv("STRATEGY_WARMUP_SEC", "30"))          # 기동 후 신규 진입/데드크로스 청산 보류(초)
STRATEGY_MAX_POSITIONS = int(os.getenv("STRATEGY_MAX_POSITIONS", "10"))    # 계정당 동시 보유 종목 수 상한(현금 소진·과분산 방지)

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
