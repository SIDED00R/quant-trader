"""환경 설정 로딩 (단일 책임: 설정)."""
import os

from dotenv import load_dotenv

load_dotenv()

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

SYMBOLS = [
    s.strip()
    for s in os.getenv("SYMBOLS", "KRW-BTC,KRW-ETH,KRW-XRP,KRW-SOL,KRW-DOGE").split(",")
    if s.strip()
]

TOPIC_TICKS = "market.ticks"
