"""헬스체크 (단일 책임: liveness + 배포 sha 노출 — 수동 디버깅용).

과거 CI healthcheck 잡이 폴링했으나 그 잡은 제거됨(배포 검증은 이미지 revision 라벨 대조).
현재는 수동 curl 확인용으로 유지. DB 무의존 — 앱 liveness만 본다(의존성 장애는 각 기능
엔드포인트가 드러냄). 인증 예외(security._is_public).
"""
import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    """{"ok": true, "sha": <빌드 시 주입된 GIT_SHA, 로컬은 dev>}"""
    return {"ok": True, "sha": os.environ.get("GIT_SHA", "dev")}
