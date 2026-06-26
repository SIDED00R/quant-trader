"""업비트 분봉 수집·캐시 검증 (합성 데이터, 네트워크 불필요).

- 로드/merge 경로: 정렬 캐시를 (ts, symbol) 전역순으로 BTick(종가) yield.
- 백필 경로: REST 페이지네이션·재시도(429/5xx)·미마감 봉 제외·finalize 정렬/중복제거.
  httpx는 가짜 클라이언트로 대체하고 time.sleep은 패치해 즉시 수행한다.
"""
import csv
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest import mock

import httpx

from common import http_client as _hc
from backtest import upbit_candles as uc
from backtest.upbit_candles import _HEADER, cache_path, load


def _write(cache_dir, market, unit, rows):
    """rows: [(ts_ms, close), ...]. OHLC=close, volume=0, dt_utc='x'(로드 경로는 미사용)."""
    p = cache_path(cache_dir, market, unit)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for ts, close in rows:
            w.writerow([ts, close, close, close, close, 0, "x"])


def _write_rows(cache_dir, market, unit, rows):
    """rows: 완전한 _HEADER 행([ts_ms, open, high, low, close, volume, dt_utc])."""
    p = cache_path(cache_dir, market, unit)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        w.writerows(rows)


def _candle(dt_utc, close, o=None, h=None, l=None, vol=0.0):
    """업비트 분봉 응답 dict(필요 필드만)."""
    return {
        "candle_date_time_utc": dt_utc,
        "opening_price": o if o is not None else close,
        "high_price": h if h is not None else close,
        "low_price": l if l is not None else close,
        "trade_price": close,
        "candle_acc_trade_volume": vol,
    }


class _Resp:
    """가짜 httpx 응답."""
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://test")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=httpx.Response(self.status_code, request=req))

    def json(self):
        return self._payload


class _Client:
    """미리 준비한 응답을 순차 반환하는 가짜 클라이언트. 호출 params를 기록한다."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(params)
        return self._responses.pop(0)


class TestUpbitCacheLoad(unittest.TestCase):
    def test_merge_global_time_order_and_tiebreak(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101"), (180000, "102")])
            _write(d, "KRW-ETH", 1, [(60000, "10"), (90000, "11")])
            out = list(load(["KRW-ETH", "KRW-BTC"], 1, d))  # 입력순을 일부러 ETH 먼저
            self.assertEqual(len(out), 5)
            ts = [t.ts for t in out]
            self.assertEqual(ts, sorted(ts), "전역 시간순이어야 함")
            # 동일 ts(60.0) tie-break는 입력순이 아니라 symbol 사전순(ts, symbol) → BTC 먼저
            self.assertEqual([(out[0].symbol, out[0].ts), (out[1].symbol, out[1].ts)],
                             [("KRW-BTC", 60.0), ("KRW-ETH", 60.0)])
            self.assertEqual([t for t in out if t.ts == 90.0][0].price, Decimal("11"))

    def test_range_filter_start_inclusive_end_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101"), (180000, "102")])
            out = list(load(["KRW-BTC"], 1, d, start_ms=120000, end_ms=180000))
            self.assertEqual([t.ts for t in out], [120.0])
            self.assertEqual(out[0].price, Decimal("101"))

    def test_missing_cache_yields_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list(load(["KRW-NOPE"], 1, d)), [])

    def test_empty_header_only_cache_yields_nothing(self):
        # 파일은 있으나 헤더만(데이터 0행) — 파일 부재와 구분되는 경로
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [])
            self.assertEqual(list(load(["KRW-BTC"], 1, d)), [])

    def test_mixed_present_and_missing_markets(self):
        # 한 종목만 백필된 현실적 상황: 없는 스트림은 조용히 빈 기여, 있는 스트림은 정상 yield
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101")])
            out = list(load(["KRW-BTC", "KRW-NOPE"], 1, d))
            self.assertEqual([(t.symbol, t.ts) for t in out],
                             [("KRW-BTC", 60.0), ("KRW-BTC", 120.0)])

    def test_close_column_drives_price(self):
        # OHLC를 서로 다르게 써서 price가 정확히 close 컬럼에서 오는지 고정(open/high/low 회귀 검출)
        with tempfile.TemporaryDirectory() as d:
            _write_rows(d, "KRW-BTC", 1, [[60000, "99", "110", "98", "100", 0, "2026-01-01T00:01:00"]])
            out = list(load(["KRW-BTC"], 1, d))
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0].price, Decimal("100"))

    def test_unsorted_cache_raises(self):
        # finalize 안 된(시간 역순) 캐시는 조용히 잘못된 결과 대신 에러로 막는다
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(180000, "102"), (120000, "101"), (60000, "100")])
            with self.assertRaises(ValueError):
                list(load(["KRW-BTC"], 1, d))

    def test_load_does_not_dedup_equal_ts(self):
        # 중복 제거는 _finalize의 책임이지 load의 책임이 아니다 — 같은 ts는 둘 다 통과(에러 아님)
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (60000, "999"), (120000, "101")])
            out = list(load(["KRW-BTC"], 1, d))
            self.assertEqual([t.ts for t in out], [60.0, 60.0, 120.0])


class TestResample(unittest.TestCase):
    def test_downsample_keeps_last_close_of_bucket(self):
        # 1분봉 6개를 5분봉으로 → 버킷[0,5분),[5,10분) 각각 마지막 종가, ts는 그 마지막 봉 시각
        with tempfile.TemporaryDirectory() as d:
            rows = [(i * 60000, str(100 + i)) for i in range(7)]  # ts=0,60k,...,360k(=6분)
            _write(d, "KRW-BTC", 1, rows)
            out = list(load(["KRW-BTC"], 1, d, bar_min=5))
            # 버킷1: ts 0~240k(분0~4) 마지막=분4(ts=240k,close=104). 버킷2: 분5~6 마지막=분6(ts=360k,close=106)
            self.assertEqual([(t.ts, t.price) for t in out],
                             [(240.0, Decimal("104")), (360.0, Decimal("106"))])

    def test_resample_preserves_global_order_across_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(i * 60000, str(100 + i)) for i in range(10)])
            _write(d, "KRW-ETH", 1, [(i * 60000, str(10 + i)) for i in range(10)])
            out = list(load(["KRW-ETH", "KRW-BTC"], 1, d, bar_min=5))
            ts = [t.ts for t in out]
            self.assertEqual(ts, sorted(ts), "리샘플 후에도 전역 시간순")

    def test_no_bar_min_is_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "KRW-BTC", 1, [(60000, "100"), (120000, "101"), (180000, "102")])
            out = list(load(["KRW-BTC"], 1, d, bar_min=None))
            self.assertEqual([t.ts for t in out], [60.0, 120.0, 180.0])


class TestUpbitBackfill(unittest.TestCase):
    def test_ws_ms_converts_candle_time_to_utc_epoch_ms(self):
        expected = int(datetime(2026, 1, 2, 3, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(uc._ws_ms({"candle_date_time_utc": "2026-01-02T03:04:00"}), expected)
        # 1분 간격은 정확히 60_000 ms (KST 9h 오프셋 같은 오해석이면 깨짐)
        a = uc._ws_ms({"candle_date_time_utc": "2026-01-02T03:04:00"})
        b = uc._ws_ms({"candle_date_time_utc": "2026-01-02T03:05:00"})
        self.assertEqual(b - a, 60_000)

    def test_scan_dt_returns_oldest_and_newest(self):
        with tempfile.TemporaryDirectory() as d:
            _write_rows(d, "KRW-BTC", 1, [
                [60000, "1", "1", "1", "1", 0, "2026-01-01T00:01:00"],
                [180000, "3", "3", "3", "3", 0, "2026-01-01T00:03:00"],
                [120000, "2", "2", "2", "2", 0, "2026-01-01T00:02:00"],
            ])
            oldest, newest = uc._scan_dt(cache_path(d, "KRW-BTC", 1))
            self.assertEqual(oldest, datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc))
            self.assertEqual(newest, datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc))

    def test_scan_dt_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(uc._scan_dt(cache_path(d, "KRW-NOPE", 1)), (None, None))

    def test_finalize_sorts_and_dedups_keeping_last(self):
        with tempfile.TemporaryDirectory() as d:
            p = cache_path(d, "KRW-BTC", 1)
            _write_rows(d, "KRW-BTC", 1, [
                [180000, "x", "x", "x", "old", 0, "2026-01-01T00:03:00"],
                [60000, "x", "x", "x", "100", 0, "2026-01-01T00:01:00"],
                [180000, "x", "x", "x", "new", 0, "2026-01-01T00:03:00"],  # 같은 ts 재등장
                [120000, "x", "x", "x", "101", 0, "2026-01-01T00:02:00"],
            ])
            uc._finalize(p, "KRW-BTC", log=lambda *a: None)
            with open(p, newline="", encoding="utf-8") as f:
                got = list(csv.DictReader(f))
            self.assertEqual([int(r["ts_ms"]) for r in got], [60000, 120000, 180000])  # 오름차순
            self.assertEqual(got[-1]["close"], "new")  # 중복 ts는 파일상 마지막 행이 이김

    def test_get_retries_on_429_then_succeeds(self):
        client = _Client([_Resp(429), _Resp(429), _Resp(200, [{"x": 1}])])
        with mock.patch.object(_hc.time, "sleep") as sl:
            out = uc._get(client, 1, {"market": "KRW-BTC", "count": 200}, req_sleep=0)
        self.assertEqual(out, [{"x": 1}])
        self.assertEqual(len(client.calls), 3)
        self.assertGreaterEqual(sl.call_count, 2)

    def test_get_retries_on_5xx_then_succeeds(self):
        # 일시적 5xx도 429처럼 재시도해야 한다(전체 백필이 한 번의 서버 오류로 죽지 않게)
        client = _Client([_Resp(503), _Resp(500), _Resp(200, [{"ok": True}])])
        with mock.patch.object(_hc.time, "sleep"):
            out = uc._get(client, 1, {"market": "KRW-BTC"}, req_sleep=0)
        self.assertEqual(out, [{"ok": True}])
        self.assertEqual(len(client.calls), 3)

    def test_get_raises_after_exhausting_retries(self):
        client = _Client([_Resp(429)] * uc._MAX_RETRIES)
        with mock.patch.object(_hc.time, "sleep"):
            with self.assertRaises(RuntimeError):
                uc._get(client, 1, {"market": "KRW-BTC"}, req_sleep=0)
        self.assertEqual(len(client.calls), uc._MAX_RETRIES)

    def test_fetch_backward_paginates_and_stops_on_short_page(self):
        far_future = 10 ** 15  # complete_until_ms 매우 큼 → 미마감 스킵 없음
        page1 = [_candle("2026-01-01T00:03:00", "3"), _candle("2026-01-01T00:02:00", "2")]  # full(=_PAGE)
        page2 = [_candle("2026-01-01T00:01:00", "1")]                                        # short → 종료
        client = _Client([_Resp(200, page1), _Resp(200, page2)])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.csv")
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                with mock.patch.object(uc, "_PAGE", 2), mock.patch.object(_hc.time, "sleep"):
                    n = uc._fetch_backward(client, w, fh, "KRW-BTC", 1, None, None, far_future,
                                           req_sleep=0, log=lambda *a: None)
            with open(p, newline="", encoding="utf-8") as f:
                got = list(csv.reader(f))
        self.assertEqual(n, 3)
        self.assertEqual(len(got), 3)
        self.assertIsNone(client.calls[0].get("to"))                  # 첫 요청은 to 없음
        self.assertEqual(client.calls[1]["to"], "2026-01-01T00:02:00Z")  # 커서=직전 페이지 최古
        self.assertEqual(len(client.calls), 2)

    def test_fetch_backward_stops_on_lower_bound(self):
        lower = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)  # 02:00 이하면 중단
        page = [_candle("2026-01-01T00:03:00", "3"), _candle("2026-01-01T00:02:00", "2")]
        client = _Client([_Resp(200, page)])  # 한 페이지만 준비(추가 요청하면 IndexError로 실패)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.csv")
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                with mock.patch.object(uc, "_PAGE", 2), mock.patch.object(_hc.time, "sleep"):
                    uc._fetch_backward(client, w, fh, "KRW-BTC", 1, None, lower, 10 ** 15,
                                       req_sleep=0, log=lambda *a: None)
        self.assertEqual(len(client.calls), 1)  # oldest(02:00)<=lower → 1페이지 후 종료

    def test_fetch_backward_skips_in_progress_minute(self):
        # complete_until_ms = 00:03:00 → 진행 중인 00:03 봉은 기록 제외, 마감된 00:02/00:01만 기록
        cu = uc._ws_ms({"candle_date_time_utc": "2026-01-01T00:03:00"})
        page = [_candle("2026-01-01T00:03:00", "inprogress"),
                _candle("2026-01-01T00:02:00", "2"),
                _candle("2026-01-01T00:01:00", "1")]
        client = _Client([_Resp(200, page)])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.csv")
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                with mock.patch.object(_hc.time, "sleep"):
                    uc._fetch_backward(client, w, fh, "KRW-BTC", 1, None, None, cu,
                                       req_sleep=0, log=lambda *a: None)
            with open(p, newline="", encoding="utf-8") as f:
                closes = [r[4] for r in csv.reader(f)]  # close 컬럼 index 4
        self.assertNotIn("inprogress", closes)
        self.assertEqual(closes, ["2", "1"])


if __name__ == "__main__":
    unittest.main()
