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

# 기본 커맨드 없음 — compose에서 서비스별 command로 지정
