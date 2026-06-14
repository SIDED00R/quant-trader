"""웹 대시보드 인증 (단일 책임: Basic Auth 검증).

WEB_PASSWORD 가 비어 있으면 인증을 건너뛴다(로컬 개발). 운영에서는 환경변수로 설정.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from common.config import WEB_PASSWORD, WEB_USER

_basic = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not WEB_PASSWORD:  # 인증 비활성(로컬)
        return
    ok = credentials is not None and (
        secrets.compare_digest(credentials.username, WEB_USER)
        and secrets.compare_digest(credentials.password, WEB_PASSWORD)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
