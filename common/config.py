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
