"""틱 메시지 파싱 (단일 책임: Kafka 틱 JSON → ClickHouse 행 — COLUMNS_TICKS 순서).

코인(ticks)·주식(stock_ticks) 싱크가 같은 Tick 스키마를 쓰므로 행 변환도 단일 출처로 공유한다
(컬럼 순서는 common.constants.COLUMNS_TICKS와 동기 — 한쪽만 고치는 발산 방지).
"""
import json
from datetime import datetime


def parse_row(value: bytes) -> list:
    t = json.loads(value)
    return [
        t["symbol"],
        float(t["price"]),
        float(t["volume"]),
        t["side"],
        datetime.fromisoformat(t["trade_ts"]),
        int(t["seq"]),
    ]
