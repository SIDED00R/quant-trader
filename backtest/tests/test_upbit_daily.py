"""업비트 일봉 수집 검증 (mocked httpx — 네트워크 불필요)."""
import unittest
from datetime import datetime, timezone
from unittest import mock

from backtest import upbit_daily as ud


def _candle(date_str, close):
    """업비트 일봉 응답 dict(필요 필드만). date_str='2026-06-19' → 00:00:00."""
    return {
        "candle_date_time_utc": f"{date_str}T00:00:00",
        "opening_price": close, "high_price": close, "low_price": close,
        "trade_price": close, "candle_acc_trade_volume": 1.0,
    }


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    """준비한 페이지를 순차 반환하는 가짜 httpx 클라이언트(컨텍스트 매니저)."""
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append(params)
        return _Resp(self._pages.pop(0) if self._pages else [])


def _run(pages, days=4000, complete_until="2026-06-20"):
    cu = datetime.fromisoformat(f"{complete_until}T00:00:00").replace(tzinfo=timezone.utc)
    client = _Client(pages)
    with mock.patch.object(ud.httpx, "Client", return_value=client), \
         mock.patch.object(ud.time, "sleep"):
        rows = ud.fetch_daily("KRW-BTC", days, cu, req_sleep=0, log=lambda *a: None)
    return rows, client


class TestFetchDaily(unittest.TestCase):
    def test_excludes_in_progress_today_and_sorts_ascending(self):
        # 응답은 최신순(today, 19, 18). today(=complete_until)는 미마감이라 제외, 결과는 오름차순
        page = [_candle("2026-06-20", 300), _candle("2026-06-19", 200), _candle("2026-06-18", 100)]
        rows, _ = _run([page])
        self.assertEqual([r[1].date().isoformat() for r in rows], ["2026-06-18", "2026-06-19"])
        self.assertEqual([r[5] for r in rows], [100, 200])  # close 컬럼(index 5)

    def test_stops_when_page_below_full(self):
        # 한 페이지(<_PAGE)면 추가 요청 없음
        page = [_candle("2026-06-19", 200), _candle("2026-06-18", 100)]
        _, client = _run([page])
        self.assertEqual(len(client.calls), 1)

    def test_paginates_with_to_until_cutoff(self):
        # _PAGE=2로 줄여 2페이지 페이지네이션 + cutoff 도달 종료 검증
        p1 = [_candle("2026-06-19", 2), _candle("2026-06-18", 1)]
        p2 = [_candle("2026-06-17", 0.5), _candle("2026-06-16", 0.25)]
        with mock.patch.object(ud, "_PAGE", 2):
            rows, client = _run([p1, p2], days=4000)
        self.assertGreaterEqual(len(client.calls), 2)
        self.assertIn("to", client.calls[1])  # 2번째 요청은 to= 역방향
        self.assertEqual([r[1].date().isoformat() for r in rows],
                         ["2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19"])

    def test_cutoff_stops_pagination(self):
        # days=1이면 cutoff가 최근이라 첫 페이지(오래된 봉 포함)에서 즉시 종료
        page = [_candle("2026-06-19", 2), _candle("2020-01-01", 1)]  # 오래된 봉 포함
        with mock.patch.object(ud, "_PAGE", 2):
            _, client = _run([page, page], days=1)
        self.assertEqual(len(client.calls), 1)  # oldest<=cutoff → 추가요청 없음


class TestUpsert(unittest.TestCase):
    def test_upsert_inserts_rows(self):
        calls = []

        class FakeCH:
            def insert(self, table, rows, column_names=None):
                calls.append((table, len(rows), column_names))

        n = ud.upsert_clickhouse(FakeCH(), [["KRW-BTC", datetime.now(timezone.utc), 1, 1, 1, 1, 1]])
        self.assertEqual(n, 1)
        self.assertEqual(calls[0][0], "candles_1d")
        self.assertEqual(calls[0][2], ud._COLUMNS)

    def test_upsert_empty_noop(self):
        self.assertEqual(ud.upsert_clickhouse(None, []), 0)


if __name__ == "__main__":
    unittest.main()
