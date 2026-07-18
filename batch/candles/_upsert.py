"""캔들 ClickHouse 적재 코어 (단일 책임: rows → insert, 멱등 가드·행수 반환).

upbit_daily(코인 일봉)·toss_daily_load(주식 일봉)·toss_intraday(주식 분봉)가 컬럼·기본
테이블만 달리해 공유한다 — 동일 구현 3벌의 발산 방지. ReplacingMergeTree라 재실행 멱등.
"""


def upsert(client, rows: list, table: str, columns: list) -> int:
    if not rows:
        return 0
    client.insert(table, rows, column_names=columns)
    return len(rows)
