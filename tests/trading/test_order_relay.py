"""주문 아웃박스 릴레이 검증 (order_relay.run — 가짜 pool/producer, DB/Kafka 무접촉).

핵심 계약: ① 발행(produce)→flush→published 마킹(UPDATE)의 순서(순서 뒤집히면 outbox 유실 위험)와
produce 인자(토픽/key/value 인코딩) ② 빈 배치는 sleep 후 재폴링만 하고 발행·마킹 없음
③ produce 예외 시 published 마킹이 일어나지 않음(at-least-once 보존 — 재기동 시 재발행 가능).
무한루프는 patched time.sleep 또는 pool.connection이 sentinel 예외를 던져 탈출시킨다.
"""
import unittest
from unittest.mock import MagicMock, patch

from common.config import TOPIC_ORDERS
from trading.relay import order_relay


class _StopLoop(Exception):
    """무한루프 탈출용 sentinel(운영 코드에 없는 테스트 전용 예외)."""


def _make_conn(select_rows_sequence):
    """conn.execute 스텁: SELECT 호출마다 select_rows_sequence에서 순서대로 rows를 꺼내 fetchall에 반영."""
    conn = MagicMock()
    seq = list(select_rows_sequence)

    def _exec(sql, *args, **kwargs):
        result = MagicMock()
        if sql.strip().startswith("SELECT"):
            result.fetchall.return_value = seq.pop(0)
        return result

    conn.execute.side_effect = _exec
    return conn


def _patched(pool, producer, sleep_side_effect):
    return (
        patch.object(order_relay, "open_pool"),
        patch.object(order_relay, "close_pool"),
        patch.object(order_relay, "pool", pool),
        patch.object(order_relay, "create_producer", return_value=producer),
        patch.object(order_relay.time, "sleep", side_effect=sleep_side_effect),
    )


class TestPublishFlushMarkOrder(unittest.TestCase):
    def test_produce_then_flush_then_mark_published_in_order(self):
        rows = [(1, "BTC", '{"a":1}'), (2, "ETH", '{"b":2}')]
        conn = _make_conn([rows, []])  # 1회차: 배치 처리, 2회차: 빈 배치 → sleep에서 탈출
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        producer = MagicMock()

        parent = MagicMock()
        parent.attach_mock(producer, "producer")
        parent.attach_mock(conn, "conn")

        patches = _patched(pool, producer, _StopLoop())
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaises(_StopLoop):
                order_relay.run()

        names = [c[0] for c in parent.mock_calls]
        produce_idx = names.index("producer.produce")
        flush_idx = names.index("producer.flush")
        update_idx = next(
            i for i, c in enumerate(parent.mock_calls)
            if c[0] == "conn.execute" and c[1][0].strip().startswith("UPDATE")
        )
        self.assertLess(produce_idx, flush_idx)
        self.assertLess(flush_idx, update_idx)

        first_produce = producer.produce.call_args_list[0]
        self.assertEqual(first_produce.args[0], TOPIC_ORDERS)
        self.assertEqual(first_produce.kwargs["key"], b"BTC")
        self.assertEqual(first_produce.kwargs["value"], b'{"a":1}')
        second_produce = producer.produce.call_args_list[1]
        self.assertEqual(second_produce.kwargs["key"], b"ETH")

        update_call = parent.mock_calls[update_idx]
        update_sql, update_params = update_call[1]
        self.assertIn("published=TRUE", update_sql)
        self.assertEqual(update_params, ([1, 2],))


class TestEmptyBatchSleepsWithoutPublish(unittest.TestCase):
    def test_empty_batch_polls_again_without_produce_or_mark(self):
        conn = _make_conn([[]])
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        producer = MagicMock()

        patches = _patched(pool, producer, _StopLoop())
        with patches[0], patches[1], patches[2], patches[3], patches[4] as sleep_mock:
            with self.assertRaises(_StopLoop):
                order_relay.run()
            sleep_mock.assert_called_once_with(order_relay.POLL_SEC)

        producer.produce.assert_not_called()
        update_calls = [c for c in conn.execute.call_args_list if c.args[0].strip().startswith("UPDATE")]
        self.assertEqual(update_calls, [])


class TestProduceExceptionPreventsMark(unittest.TestCase):
    def test_produce_failure_leaves_published_unmarked(self):
        rows = [(1, "BTC", '{"a":1}')]
        conn = _make_conn([rows])
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        producer = MagicMock()
        producer.produce.side_effect = RuntimeError("kafka down")

        patches = _patched(pool, producer, _StopLoop())
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaises(RuntimeError):
                order_relay.run()

        update_calls = [c for c in conn.execute.call_args_list if c.args[0].strip().startswith("UPDATE")]
        self.assertEqual(update_calls, [])


if __name__ == "__main__":
    unittest.main()
