"""텔레그램 웹훅 → GCE 온디맨드 매매 VM 시작/정지.

온디맨드 매매 VM(coin-trade-vm)을 수동으로 깨우거나 끄기 위한 gen2 Cloud Function.
텔레그램 봇 명령(/start_vm, /stop_vm, /status)을 받아 Compute Engine API로 인스턴스를 start/stop 한다.
평소 매매는 Cloud Scheduler가 깨우고 VM이 잡 종료 후 스스로 poweroff 하므로, 본 봇은 주로 수동 기동·디버깅용.

⚠️ 제어 대상은 온디맨드 매매 VM만. 틱 수집 전용 상시 VM은 정지하면 24/7 틱이 끊기므로 노출하지 않는다.
보안: 텔레그램 secret 토큰 헤더 + 발신 chat_id 화이트리스트 이중 검증. 실패 시 200 반환(재시도 억제).
"""

import logging
import os

import functions_framework
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT = os.environ["GCP_PROJECT"]
ZONE = os.environ["GCE_ZONE"]
INSTANCE = os.environ["GCE_INSTANCE"]
ALLOWED_CHAT_ID = os.environ["TELEGRAM_ALLOWED_CHAT_ID"].strip()
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
# GCE 인스턴스 상태: RUNNING/TERMINATED가 안정 상태, 나머지는 전환 중.
_STABLE = {"RUNNING", "TERMINATED", "SUSPENDED"}
_BUSY_MSG = "⏳ VM 상태 전환 중입니다. 잠시 후 /status 로 확인하세요."
_HELP = (
    "🖥️ 매매 VM 제어 봇\n"
    "/start_vm — 매매 VM 기동(대시보드 조회 모드; 부팅 후 SSH 터널 안내가 옴)\n"
    "/stop_vm — 매매 VM 정지(비용 절감·다음 예약 매매 정상화)\n"
    "/status — 현재 상태 확인"
)


def _gce():
    return build("compute", "v1", cache_discovery=False)


def _state() -> str:
    """RUNNING / TERMINATED / STOPPING / PROVISIONING / STAGING / SUSPENDING ..."""
    return _gce().instances().get(project=PROJECT, zone=ZONE, instance=INSTANCE).execute()["status"]


def _start() -> None:
    _gce().instances().start(project=PROJECT, zone=ZONE, instance=INSTANCE).execute()


def _stop() -> None:
    _gce().instances().stop(project=PROJECT, zone=ZONE, instance=INSTANCE).execute()


def _set_boot_mode(mode: str) -> None:
    """다음 부팅 모드 설정(metadata vm-boot-mode). startup이 읽어 dashboard(조회)/trade(예약매매) 분기."""
    inst = _gce().instances().get(project=PROJECT, zone=ZONE, instance=INSTANCE).execute()
    md = inst.get("metadata", {})
    items = [i for i in md.get("items", []) if i.get("key") != "vm-boot-mode"]
    items.append({"key": "vm-boot-mode", "value": mode})
    _gce().instances().setMetadata(
        project=PROJECT, zone=ZONE, instance=INSTANCE,
        body={"fingerprint": md.get("fingerprint"), "items": items}).execute()


def _status_msg() -> str:
    st = _state()
    if st == "RUNNING":
        return "켜짐 ✅ 사용 가능"
    if st == "TERMINATED":
        return "정지됨 — /start_vm 으로 기동하세요"
    return f"전환 중… ({st})"


def _reply(chat_id, text: str) -> None:
    try:
        requests.post(f"{_API_BASE}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except requests.RequestException:
        pass  # 응답 실패는 무시 — 명령 자체는 이미 처리됨


def _dispatch(command: str, chat_id) -> None:
    try:
        if command == "/start_vm":
            st = _state()
            if st == "RUNNING":
                _reply(chat_id, "이미 켜져 있습니다. /status 로 확인하세요.")
            elif st not in _STABLE:
                _reply(chat_id, _BUSY_MSG)
            else:
                _set_boot_mode("dashboard")     # 수동 기동=대시보드 조회 모드(매매 잡·poweroff 스킵)
                _start()
                _reply(chat_id, "✅ 매매 VM 대시보드 모드로 기동 요청. 부팅(~1분) 후 SSH 터널 안내가 텔레그램으로 옵니다. 조회 끝나면 /stop_vm.")
        elif command == "/stop_vm":
            st = _state()
            if st == "TERMINATED":
                _reply(chat_id, "이미 정지돼 있습니다.")
            elif st not in _STABLE:
                _reply(chat_id, _BUSY_MSG)
            else:
                _set_boot_mode("trade")         # 다음 예약 부팅이 정상 매매하도록 리셋
                _stop()
                _reply(chat_id, "🛑 매매 VM 정지 요청 완료. 비용이 절감됩니다.")
        elif command == "/status":
            _reply(chat_id, f"📊 상태: {_status_msg()}")
        else:
            _reply(chat_id, _HELP)
    except Exception as e:  # noqa: BLE001 — 모든 API 오류를 사용자에게 회신
        if isinstance(e, HttpError) and e.resp.status == 409:  # 다른 작업이 진행 중
            _reply(chat_id, _BUSY_MSG)
            return
        logging.exception("command %s failed", command)
        _reply(chat_id, f"⚠️ 오류: {type(e).__name__}: {e}")


@functions_framework.http
def handle(request):
    # 1) secret 토큰 헤더 검증
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return ("", 200)

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return ("", 200)

    chat_id = message.get("chat", {}).get("id")
    # 2) chat_id 화이트리스트 검증
    if str(chat_id) != ALLOWED_CHAT_ID:
        return ("", 200)

    text = (message.get("text") or "").strip()
    # "/start_vm@MyBot" → "/start_vm"
    command = text.split()[0].split("@")[0] if text else ""
    _dispatch(command, chat_id)
    return ("", 200)
