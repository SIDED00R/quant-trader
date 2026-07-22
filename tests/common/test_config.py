"""설정 로딩 검증 (pydantic-settings 전환).

핵심 계약: ① 타입 강제(Decimal/bool/int) ② 잘못된 값 fail-fast(ValidationError)
③ 모듈 파생값(CSV→list/set·분기 URL·DSN)이 Settings raw와 일관 ④ api SESSION_SECRET fail-fast.
"""
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

import common.config as config
from common.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestSettingsTypes(unittest.TestCase):
    """_env_file=None으로 로컬 .env 무시 → kwargs 강제 파싱만 검증(결정적)."""
    def test_decimal_coercion(self):
        s = Settings(_env_file=None, FEE_RATE="0.001")
        self.assertEqual(s.FEE_RATE, Decimal("0.001"))
        self.assertIsInstance(s.FEE_RATE, Decimal)

    def test_int_coercion(self):
        s = Settings(_env_file=None, SMA_LONG="50")
        self.assertEqual(s.SMA_LONG, 50)
        self.assertIsInstance(s.SMA_LONG, int)

    def test_bool_coercion(self):
        self.assertFalse(Settings(_env_file=None, KIS_MOCK="false").KIS_MOCK)
        self.assertTrue(Settings(_env_file=None, KIS_MOCK="true").KIS_MOCK)

    def test_bad_int_fails_fast(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, SMA_LONG="notanumber")

    def test_declared_defaults(self):
        # 선언된 기본값이 구 os.getenv 기본과 동일한지(env 무관 — 필드 default 직접 확인).
        d = Settings.model_fields
        self.assertEqual(d["CLICKHOUSE_DB"].default, "coin_analytics")
        self.assertEqual(d["POSTGRES_USER"].default, "trader")
        self.assertEqual(d["SESSION_SECRET"].default, "dev-insecure-change-me")
        self.assertEqual(d["FEE_RATE"].default, Decimal("0.0005"))
        self.assertEqual(d["SMA_LONG"].default, 25)


class TestModuleDerivation(unittest.TestCase):
    """모듈 파생값이 Settings raw와 일관(env 무관 불변식)."""
    def test_symbols_list_matches_raw(self):
        for name in ("SYMBOLS", "STOCK_SYMBOLS", "ENSEMBLE_SYMBOLS"):
            raw = getattr(config._settings, name)
            expected = [x.strip() for x in raw.split(",") if x.strip()]
            self.assertEqual(getattr(config, name), expected)

    def test_allowed_emails_is_set(self):
        self.assertIsInstance(config.ALLOWED_EMAILS, set)

    def test_telegram_chat_ids_is_int_set(self):
        self.assertIsInstance(config.TELEGRAM_ALLOWED_CHAT_IDS, set)
        self.assertTrue(all(isinstance(x, int) for x in config.TELEGRAM_ALLOWED_CHAT_IDS))

    def test_kis_base_branch(self):
        self.assertIn(config.KIS_REST_BASE, (
            "https://openapivts.koreainvestment.com:29443",
            "https://openapi.koreainvestment.com:9443"))

    def test_auth_enabled_is_bool(self):
        self.assertIsInstance(config.AUTH_ENABLED, bool)

    def test_postgres_dsn_composed(self):
        self.assertIn("host=", config.POSTGRES_DSN)
        self.assertIn(f"dbname={config._settings.POSTGRES_DB}", config.POSTGRES_DSN)
        self.assertIn("connect_timeout=", config.POSTGRES_DSN)

    def test_scalar_via_getattr(self):
        # from-import 호환: 모듈 전역 아닌 스칼라도 __getattr__로 노출
        self.assertEqual(config.CLICKHOUSE_HTTP_PORT, config._settings.CLICKHOUSE_HTTP_PORT)


class TestApiSessionFailFast(unittest.TestCase):
    """api.main 임포트 시 AUTH_ENABLED+기본 SESSION_SECRET면 기동 실패(서브프로세스로 격리)."""
    def _import_api(self, env_extra):
        return subprocess.run(
            [sys.executable, "-c", "import api.main"],
            cwd=_REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",   # 자식이 UTF-8로 쓰는 한글 메시지 디코딩
            env={**_base_env(), **env_extra},
        )

    def test_default_secret_with_auth_raises(self):
        r = self._import_api({
            "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y",
            "SESSION_SECRET": "dev-insecure-change-me",
        })
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SESSION_SECRET", r.stderr)

    def test_strong_secret_with_auth_ok(self):
        r = self._import_api({
            "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y",
            "SESSION_SECRET": "a-strong-random-secret",
        })
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_auth_disabled_default_secret_ok(self):
        # 인증 비활성(로컬)이면 기본 시크릿이어도 통과
        r = self._import_api({"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""})
        self.assertEqual(r.returncode, 0, r.stderr)


def _base_env():
    import os
    # .env 파일 간섭 차단(서브프로세스가 리포 루트에서 돌아 로컬 .env를 읽지 않게)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GOOGLE_", "SESSION_", "SITE_"))}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


if __name__ == "__main__":
    unittest.main()
