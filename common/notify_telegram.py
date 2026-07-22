"""텔레그램 매매 알림 전송 (단일 책임: MTProto 사용자 세션으로 알림 1건(텍스트/사진) 전송 — 절대 raise하지 않음).

MTProto 사용자 계정(Telethon StringSession) 방식 — 봇 아님. 세션 발급은 scripts/telegram_login.py 1회.
설정(common.config): TELEGRAM_API_ID/API_HASH/SESSION(+TELEGRAM_TARGET, 기본 'me'=나에게 보내기).
미설정이면 사유를 출력하고 False — 매매 경로를 절대 막지 않는다(모든 예외 흡수).

CLI(수동 테스트·startup 쉘 폴백용): python -m common.notify_telegram "메시지"  (인자 없으면 stdin)
"""
import asyncio
import io
import logging
import sys

from common import log
from common.config import TELEGRAM_API_HASH, TELEGRAM_API_ID, TELEGRAM_SESSION, TELEGRAM_TARGET
from common.rate_limit import acquire

logger = logging.getLogger(__name__)

_MAX_LEN = 4000        # Telegram 메시지 한도 4096 — 여유 두고 절단(로그 tail 첨부 대비)
_MAX_CAPTION = 1024    # Telegram 미디어 캡션 한도
_SEND_TIMEOUT = 60.0   # 연결+전송 전체 상한(초). 잡을 오래 붙들지 않는다.


def _target() -> str | int:
    """수신 대상 해석: 숫자면 chat id, 아니면 'me'/'@username' 그대로."""
    t = TELEGRAM_TARGET.strip() or "me"
    try:
        return int(t)
    except ValueError:
        return t


def _deliver(action) -> bool:
    """공통 가드(설정 검사·telethon 지연 import·타임아웃·예외 분류)로 action(client, target) 코루틴 실행."""
    missing = [k for k, v in (("TELEGRAM_API_ID", TELEGRAM_API_ID),
                              ("TELEGRAM_API_HASH", TELEGRAM_API_HASH),
                              ("TELEGRAM_SESSION", TELEGRAM_SESSION)) if not v.strip()]
    if missing:
        logger.warning(f"미설정: {'/'.join(missing)} — 전송 생략(Secret Manager telegram-env 확인)")
        return False
    try:
        api_id = int(TELEGRAM_API_ID)
    except ValueError:
        logger.error(f"TELEGRAM_API_ID가 숫자가 아님: {TELEGRAM_API_ID!r}")
        return False
    try:
        # 지연 import — telethon 미설치 환경(테스트·앱 상시 서비스)에서도 모듈 import는 항상 안전.
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        logger.error("telethon 미설치 — requirements.txt 반영/이미지 재빌드 필요")
        return False

    async def _run() -> None:
        client = TelegramClient(StringSession(TELEGRAM_SESSION), api_id, TELEGRAM_API_HASH,
                                connection_retries=1, timeout=10)
        async with client:
            await action(client, _target())

    try:
        acquire("telegram", "send")
        asyncio.run(asyncio.wait_for(_run(), timeout=_SEND_TIMEOUT))
        return True
    except (asyncio.TimeoutError, ConnectionError, OSError) as e:
        logger.error(f"네트워크 오류/타임아웃 — {type(e).__name__}: {e}")
    except Exception as e:
        # telethon 예외는 버전별 계층이 달라 이름으로 분류(속성 부재로 여기서 또 죽는 일 방지).
        name = type(e).__name__
        if name == "FloodWaitError":
            logger.error(f"FloodWait {getattr(e, 'seconds', '?')}s — 전송 포기")
        elif name in ("AuthKeyError", "AuthKeyInvalidError", "AuthKeyUnregisteredError",
                      "UserDeactivatedError", "SessionRevokedError", "SessionExpiredError"):
            logger.error(f"세션 무효/철회({name}: {e}) — scripts/telegram_login.py 재실행 후 "
                  "Secret Manager telegram-env의 TELEGRAM_SESSION 갱신 필요")
        else:
            logger.exception(f"전송 실패 — {name}: {e}")
    return False


def send(text: str) -> bool:
    """텔레그램으로 text 1건 전송. True=전송 성공. 실패는 원인별 진단을 출력하고 False(예외 없음)."""
    return _deliver(lambda client, target: client.send_message(target, text[:_MAX_LEN]))


def send_photo(png: bytes, caption: str = "") -> bool:
    """PNG 1장을 사진(인라인 미리보기)으로 전송 — 자산 차트 등 이미지 알림용. 실패 처리 규약은 send와 동일."""
    def _action(client, target):
        buf = io.BytesIO(png)
        buf.name = "chart.png"   # 확장자로 사진 인식(문서 첨부가 아니라 인라인 이미지로)
        return client.send_file(target, buf, caption=caption[:_MAX_CAPTION])
    return _deliver(_action)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 인자를 메시지로 전송(없으면 stdin). exit 0=전송됨, 2=실패(사유는 출력됨)."""
    log.setup()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = sys.argv[1:] if argv is None else argv
    text = " ".join(args).strip() if args and args != ["-"] else sys.stdin.read().strip()
    if not text:
        logger.warning("빈 메시지 — 전송 생략")
        return 2
    return 0 if send(text) else 2


if __name__ == "__main__":
    raise SystemExit(main())
