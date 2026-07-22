"""oauth_token.TokenCache 단위 테스트 — 발급한도 보호(KIS/키움/토스), 파일/프로세스 간 재사용.

임시 디렉터리를 _CACHE_DIR로 패치(인스턴스 생성 전 — _file은 __init__에서 계산됨)하고
request_fn을 MagicMock으로 대체해 실제 발급 호출 없이 검증한다. 핵심 계약: ① 최초 get →
request_fn 1회 + 파일 생성 ② 유효 캐시 반복 get → request_fn 미호출(발급한도 보호 핵심)
③ margin 임박 만료 시 재발급 ④ 새 인스턴스가 파일에서 복원 → request_fn 미호출(프로세스 간
재사용) ⑤ force 재발급.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from common import oauth_token


class TestTokenCache(unittest.TestCase):
    def test_initial_get_calls_request_fn_once_and_saves_file(self):
        """최초 get — request_fn 1회 호출 + 파일 캐시 생성."""
        with tempfile.TemporaryDirectory() as td, patch.object(oauth_token, "_CACHE_DIR", td):
            request_fn = MagicMock(return_value=("tok1", datetime.now(timezone.utc) + timedelta(hours=1)))
            cache = oauth_token.TokenCache(request_fn, "kis_mock")
            token = cache.get()

            self.assertEqual(token, "tok1")
            request_fn.assert_called_once()
            self.assertTrue(os.path.exists(os.path.join(td, "kis_mock.json")))

    def test_valid_cache_repeated_get_skips_request(self):
        """유효 캐시 반복 get — request_fn 미호출(발급한도 보호 핵심 계약)."""
        with tempfile.TemporaryDirectory() as td, patch.object(oauth_token, "_CACHE_DIR", td):
            request_fn = MagicMock(return_value=("tok1", datetime.now(timezone.utc) + timedelta(hours=1)))
            cache = oauth_token.TokenCache(request_fn, "kis_mock")
            cache.get()
            cache.get()
            token = cache.get()

            self.assertEqual(token, "tok1")
            self.assertEqual(request_fn.call_count, 1)

    def test_reissues_when_near_expiry_margin(self):
        """만료까지 margin(기본 10분)보다 덜 남으면 재발급된다."""
        with tempfile.TemporaryDirectory() as td, patch.object(oauth_token, "_CACHE_DIR", td):
            near_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
            request_fn = MagicMock(side_effect=[
                ("tok1", near_expiry),
                ("tok2", datetime.now(timezone.utc) + timedelta(hours=1)),
            ])
            cache = oauth_token.TokenCache(request_fn, "kis_mock")
            t1 = cache.get()
            t2 = cache.get()

            self.assertEqual(t1, "tok1")
            self.assertEqual(t2, "tok2")
            self.assertEqual(request_fn.call_count, 2)

    def test_new_instance_restores_from_file_skips_request(self):
        """새 인스턴스가 파일에서 유효 토큰을 복원 — request_fn 미호출(프로세스 간 재사용)."""
        with tempfile.TemporaryDirectory() as td, patch.object(oauth_token, "_CACHE_DIR", td):
            request_fn1 = MagicMock(return_value=("tok1", datetime.now(timezone.utc) + timedelta(hours=1)))
            oauth_token.TokenCache(request_fn1, "kis_mock").get()

            request_fn2 = MagicMock()
            cache2 = oauth_token.TokenCache(request_fn2, "kis_mock")
            token = cache2.get()

            self.assertEqual(token, "tok1")
            request_fn2.assert_not_called()

    def test_force_reissues_even_when_valid(self):
        """force=True — 유효 캐시가 있어도 재발급한다."""
        with tempfile.TemporaryDirectory() as td, patch.object(oauth_token, "_CACHE_DIR", td):
            request_fn = MagicMock(side_effect=[
                ("tok1", datetime.now(timezone.utc) + timedelta(hours=1)),
                ("tok2", datetime.now(timezone.utc) + timedelta(hours=2)),
            ])
            cache = oauth_token.TokenCache(request_fn, "kis_mock")
            cache.get()
            token = cache.get(force=True)

            self.assertEqual(token, "tok2")
            self.assertEqual(request_fn.call_count, 2)


if __name__ == "__main__":
    unittest.main()
