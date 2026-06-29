"""KRX 공용 세션 (단일 책임: pykrx 로그인 게이트 + .env 선로드).

pykrx는 import 시점에 KRX_ID/KRX_PW로 로그인하므로, 이 모듈이 pykrx import 전에 .env를 로드한다.
KRX 수집기(krx·krx_bulk·kr_index_membership)는
`from batch.data._krx_session import stock, require_login`으로 import해 이 순서를 보장받는다
(각 수집기가 dotenv·pykrx를 직접 import하지 않는다). 세션은 1시간 만료 후 자동 재로그인.
"""
from dotenv import load_dotenv

load_dotenv()                        # ← pykrx import 전에 KRX_ID/KRX_PW 주입(로그인 트리거)
from pykrx import stock              # noqa: E402  (import 시점 KRX 로그인)
from pykrx.website.comm.auth import get_auth_session  # noqa: E402


def require_login() -> None:
    """통계 엔드포인트(수급·공매도 등)는 로그인 필수 — 미인증이면 즉시 실패."""
    s = get_auth_session()
    if not (s and s.is_authenticated):
        raise RuntimeError(
            "KRX 로그인 실패 — KRX_ID/KRX_PW(.env)를 확인하세요. "
            "통계 엔드포인트(수급·공매도)는 로그인 필수입니다.")
