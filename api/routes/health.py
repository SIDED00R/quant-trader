"""헬스체크 (단일 책임: 배포 검증용 liveness + 배포 sha 노출).

CI(deploy.yml healthcheck 잡)가 배포 직후 sha 일치를 확인한다. DB 무의존 —
앱 liveness만 본다(의존성 장애는 각 기능 엔드포인트가 드러냄). 인증 예외(security._is_public).
"""
import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    """{"ok": true, "sha": <빌드 시 주입된 GIT_SHA, 로컬은 dev>}"""
    return {"ok": True, "sha": os.environ.get("GIT_SHA", "dev")}
