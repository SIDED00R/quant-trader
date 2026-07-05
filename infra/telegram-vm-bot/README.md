# telegram-vm-bot — 텔레그램으로 매매 VM 켜고 끄기

온디맨드 매매 VM(`coin-trade-vm`)을 텔레그램 명령으로 시작/정지하는 gen2 Cloud Function.
평소엔 Cloud Scheduler가 깨우고 VM이 잡 후 스스로 poweroff 하므로, 이 봇은 **수동 기동·디버깅**용이다.

> ⚠️ 틱 수집 전용 상시 VM(`coin-trader-vm`)은 이 봇으로 제어하지 않는다 — 정지하면 24/7 틱이 끊긴다.

## 명령
- `/start_vm` — 매매 VM 기동(부팅 ~1분)
- `/stop_vm` — 매매 VM 정지(비용 절감)
- `/status` — 현재 상태(RUNNING/TERMINATED/전환 중)

## 설정 순서

1. **BotFather로 봇 생성** → 봇 토큰 획득. (이 봇은 웹훅 토큰 방식으로, 알림용 MTProto 세션 `telegram-env`와 별개다.)
2. **본인 chat id 확인**: 봇과 대화 시작 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 의 `message.chat.id`.
3. **webhook secret 생성**: `openssl rand -hex 32`.
4. **봇 토큰을 Secret Manager에 저장**(프로젝트 `coin-auto-trader-jvfhgq`):
   ```bash
   printf '%s' '<봇토큰>' | gcloud secrets create telegram-bot-token \
     --project coin-auto-trader-jvfhgq --data-file=-
   # 이미 있으면: ... versions add telegram-bot-token --data-file=-
   ```
5. **함수 런타임 SA에 Compute 제어 권한 부여**(setup-cicd.sh가 자동 부여하지만 수동 시):
   ```bash
   gcloud projects add-iam-policy-binding coin-auto-trader-jvfhgq \
     --member="serviceAccount:689150179824-compute@developer.gserviceaccount.com" \
     --role="roles/compute.instanceAdmin.v1"
   ```
6. **배포**:
   ```bash
   TELEGRAM_ALLOWED_CHAT_ID=<본인chatid> TELEGRAM_WEBHOOK_SECRET=<3번값> ./deploy.sh
   ```
7. **웹훅 등록**(출력된 함수 URL 사용):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<함수URL>&secret_token=<3번값>"
   ```
8. 텔레그램에서 `/status` → `/start_vm` → `/stop_vm` 왕복 확인.

## 보안
- `--allow-unauthenticated` 함수지만, 앱단에서 ① `X-Telegram-Bot-Api-Secret-Token` 헤더 == webhook secret, ② 발신 `chat_id` == 화이트리스트 로 이중 검증한다. 불일치 시 아무 동작 없이 200 반환(재시도 억제).
