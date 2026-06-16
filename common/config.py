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

# ── 자동매매 전략 (SMA 교차) ──
SMA_SHORT = int(os.getenv("SMA_SHORT", "20"))            # 단기 이동평균(틱 수)
SMA_LONG = int(os.getenv("SMA_LONG", "60"))             # 장기 이동평균(틱 수)
STRATEGY_ORDER_KRW = Decimal(os.getenv("STRATEGY_ORDER_KRW", "1000000"))  # 매수 1회 금액
STRATEGY_COOLDOWN_SEC = int(os.getenv("STRATEGY_COOLDOWN_SEC", "60"))     # 종목별 최소 매매 간격

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
