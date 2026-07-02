FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY streaming ./streaming
COPY trading ./trading
COPY api ./api
COPY scripts ./scripts
COPY db ./db

ENV PYTHONUNBUFFERED=1

# CI가 빌드 시 주입(배포 검증 — /healthz 가 노출). 마지막에 둬서 pip 레이어 캐시에 무영향.
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

# 기본 커맨드 없음 — compose에서 서비스별 command로 지정
