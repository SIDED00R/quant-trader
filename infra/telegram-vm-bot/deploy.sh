#!/usr/bin/env bash
# telegram-vm-bot 배포 스크립트 — 온디맨드 매매 VM(coin-trade-vm)을 텔레그램으로 시작/정지.
# 사전 준비(README 참고): BotFather 봇 토큰, 본인 chat id, webhook secret.
# 봇 토큰은 Secret Manager 시크릿 telegram-bot-token 에 미리 저장(README 참고).
# 함수 런타임 SA에 roles/compute.instanceAdmin.v1 권한 필요(README 참고).
# 사용법:
#   TELEGRAM_ALLOWED_CHAT_ID=123456789 TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32) \
#     ./deploy.sh
set -euo pipefail

PROJECT=coin-auto-trader-jvfhgq
REGION=us-central1
ZONE=us-central1-a
INSTANCE=coin-trade-vm

: "${TELEGRAM_ALLOWED_CHAT_ID:?TELEGRAM_ALLOWED_CHAT_ID 를 설정하세요}"
: "${TELEGRAM_WEBHOOK_SECRET:?TELEGRAM_WEBHOOK_SECRET 를 설정하세요}"

cd "$(dirname "$0")"

gcloud functions deploy telegram-vm-bot \
  --gen2 --runtime python311 \
  --region "$REGION" --project "$PROJECT" \
  --source . --entry-point handle \
  --trigger-http --allow-unauthenticated \
  --set-env-vars "GCP_PROJECT=${PROJECT},GCE_ZONE=${ZONE},GCE_INSTANCE=${INSTANCE},TELEGRAM_ALLOWED_CHAT_ID=${TELEGRAM_ALLOWED_CHAT_ID},TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET}" \
  --set-secrets "TELEGRAM_BOT_TOKEN=telegram-bot-token:latest"

echo "배포 완료. 함수 URL 확인:"
gcloud functions describe telegram-vm-bot --gen2 --region "$REGION" --project "$PROJECT" \
  --format="value(serviceConfig.uri)"
