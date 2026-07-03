"""텔레그램 MTProto 1회 로그인 (단일 책임: StringSession 발급·출력 — 개인정보를 코드/레포에 저장하지 않음).

사용법(로컬 1회, 대화형):
  1) https://my.telegram.org 로그인 → API development tools → api_id/api_hash 발급
  2) python -m scripts.telegram_login
     → api_id/api_hash 입력 → 전화번호(+8210…) 입력 → 텔레그램 앱으로 온 코드(·2FA 비밀번호) 입력
  3) 출력된 StringSession을 Secret Manager `telegram-env`의 TELEGRAM_SESSION에 저장(레포 커밋 금지)

세션은 텔레그램 '설정 → 기기'에서 로그아웃하면 무효화된다(그 경우 재실행).
"""
import sys


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print("telethon 미설치 — 먼저: pip install telethon")
        return 1
    api_id_raw = input("api_id (my.telegram.org에서 발급): ").strip()
    if not api_id_raw.isdigit():
        print("api_id는 숫자여야 합니다.")
        return 1
    api_hash = input("api_hash: ").strip()
    if not api_hash:
        print("api_hash가 비었습니다.")
        return 1
    # 전화번호/코드/2FA는 Telethon 대화형 로그인이 직접 묻는다(한국 번호는 +82 형식, 예: +8210xxxxxxxx).
    with TelegramClient(StringSession(), int(api_id_raw), api_hash) as client:
        session = client.session.save()
        me = client.get_me()
    print()
    print(f"로그인 성공: {getattr(me, 'first_name', '')} (id={getattr(me, 'id', '?')})")
    print("아래 4줄을 Secret Manager `telegram-env`에 저장하세요 (레포/커밋 금지):")
    print()
    print(f"TELEGRAM_API_ID={api_id_raw}")
    print(f"TELEGRAM_API_HASH={api_hash}")
    print(f"TELEGRAM_SESSION={session}")
    print("TELEGRAM_TARGET=me")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
