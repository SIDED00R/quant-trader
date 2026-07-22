FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# non-root 실행 사용자(uid 1000). 소스는 --chown으로 app 소유 → 런타임 __pycache__ 쓰기 가능.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app

COPY --chown=app:app common ./common
COPY --chown=app:app streaming ./streaming
COPY --chown=app:app trading ./trading
COPY --chown=app:app api ./api
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app db ./db

ENV PYTHONUNBUFFERED=1

# CI가 빌드 시 주입 — /healthz(수동 확인용)가 노출. 배포 검증 자체는 이미지 revision 라벨 대조.
# 마지막에 둬서 pip 레이어 캐시에 무영향.
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

# 네트워크(kafka/CH/PG)·/tmp(토큰 캐시)·인메모리 차트만 쓰므로 non-root로 안전. charts 바인드는 호스트 chown(infra/fix-volume-ownership.sh).
USER app

# 기본 커맨드 없음 — compose에서 서비스별 command로 지정
