"""ClickHouse 클라이언트 (단일 책임: ClickHouse 연결)."""
import clickhouse_connect

from common.config import (
    CLICKHOUSE_CONNECT_TIMEOUT,
    CLICKHOUSE_DB,
    CLICKHOUSE_HOST,
    CLICKHOUSE_HTTP_PORT,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
    CLICKHOUSE_USER,
)


def create_client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_HTTP_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DB,
        connect_timeout=CLICKHOUSE_CONNECT_TIMEOUT,
        send_receive_timeout=CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
    )
