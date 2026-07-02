#!/bin/bash
# 온디맨드 매매 VM startup — Cloud Scheduler(메인 3잡 + 스위퍼 3잡, infra/setup-cicd.sh)가 start하면:
#   docker/repo 준비(멱등) → AR 이미지 pull(revision 라벨 검증, 실패 시 --build 폴백) → 데이터 VM SSH 터널
#   → UTC 부팅시각 분기로 1회 매매 → 터널 정리 → poweroff(self-stop).
# 분기표(스케줄러와 정합):
#   UTC 06·07    → KR 주식(stock-trade-once)  ← kr-close 15:00 KST + sweep 15:10 KST (06:00/06:10 UTC, 평일)
#   UTC 19·20·21 → US 주식(us-trade-once)     ← us-close 15:30 ET + sweep 15:45 ET (EDT 19:30/45·EST 20:30/45, 평일)
#   그 외(01·02) → 코인(trade-once)           ← daily 01:00 + sweep 02:00 UTC (매일)
#   (07·21은 부팅지연 여유. 임의 시각 수동 start는 코인 분기 — 목표 수렴형이라 무해)
# 스위퍼 = 존 용량 부족(ZONE_RESOURCE_POOL_EXHAUSTED) 등 기동 실패 재시도. 이중 실행은 멱등:
#   코인=목표 수렴(재실행 시 주문 0), 주식=주간 마커(week_done)가 skip.
# DB는 데이터 VM에서 loopback 유지, 매매 VM이 터널(-L 5432/8123)로 접속(network_mode:host).
# 터널 개인키는 /etc/tunnel_key(600), 공개키는 데이터 VM 메타데이터(tunnel 사용자)에 등록돼 있어야 한다.
# 루트로 실행. -e 제외(어떤 실패에도 마지막 poweroff까지 도달하도록).
set -uxo pipefail

DATA_VM_IP=10.128.0.2
TUNNEL_KEY=/etc/tunnel_key
REPO=/opt/coin-auto-trader   # 경로는 기존 VM 유지(.env 등 보존) — 레포명 quant-trader와 무관
AR=us-central1-docker.pkg.dev/coin-auto-trader-jvfhgq/docker
APP_IMG=$AR/quant-trader-app:latest
BATCH_IMG=$AR/quant-trader-batch:latest

# ── docker/swap 준비(최초 부팅만 실제 설치, 이후 멱등) ──
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update && apt-get install -y ca-certificates curl git
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# ── 최신 코드 ──
# GitHub deploy key (private repo) — Secret Manager → SSH 키, git을 SSH로
mkdir -p /root/.ssh && chmod 700 /root/.ssh
DK_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])" 2>/dev/null || true)
curl -s -H "Authorization: Bearer $DK_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/github-deploy-key/versions/latest:access" 2>/dev/null \
  | python3 -c "import sys,json,base64;sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)['payload']['data']))" > /root/.ssh/id_ed25519 2>/dev/null || true
chmod 600 /root/.ssh/id_ed25519 2>/dev/null || true
export GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[ -d "$REPO" ] || git clone git@github.com:SIDED00R/quant-trader.git "$REPO"
cd "$REPO" && git remote set-url origin git@github.com:SIDED00R/quant-trader.git && git fetch origin main && git reset --hard origin/main
cp -n .env.example .env || true

# ── KIS 자격증명 주입 (Secret Manager kis-env → .env, VM 토큰으로 REST 접근; 실패해도 진행) ──
KIS_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "${KIS_TOKEN:-}" ]; then
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/kis-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/kis-env 2>/dev/null || true
  if [ -s /tmp/kis-env ]; then grep -v '^KIS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/kis-env > .env 2>/dev/null || true; rm -f /tmp/env.nok /tmp/kis-env; fi
fi

# ── Artifact Registry 로그인 (gcloud 불요 — VM 메타데이터 토큰. 실패해도 진행: 아래 --build 폴백) ──
echo "${DK_TOKEN:-}" | docker login -u oauth2accesstoken --password-stdin https://us-central1-docker.pkg.dev || true

# ── 데이터 VM SSH 터널(loopback DB를 매매 VM 호스트로 포워딩) ──
pkill -f "ssh.*tunnel@" 2>/dev/null || true
ssh -i "$TUNNEL_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ExitOnForwardFailure=yes \
    -fN -L 5432:localhost:5432 -L 8123:localhost:8123 tunnel@"$DATA_VM_IP" </dev/null >/dev/null 2>&1
sleep 5

# ── 이미지 신선도 (#94/#99 불변식의 계승) ──
# CI가 org.opencontainers.image.revision=<sha> 라벨로 이미지를 굽는다. pull한 :latest의 라벨이
# 위 git reset 결과(HEAD)와 다르거나 pull이 실패하면 --build로 폴백해 최신 소스로 재빌드한다.
# 낡은 코드가 조용히 실행되는 일을 원천 차단 — **이 폴백 제거 금지**.
build_flag() {  # $1=compose 서비스명 $2=이미지 ref → ""(pull 신선) 또는 "--build"
  docker compose --profile trade pull -q "$1" >/dev/null 2>&1 || { echo "--build"; return; }
  [ "$(docker image inspect "$2" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null)" \
    = "$(git rev-parse HEAD)" ] || echo "--build"
}

# ── 기동 시각으로 분기 (여러 스케줄러가 같은 VM을 start; 메타데이터 전달 채널 없어 UTC 시각으로 구분) ──
BOOT_HOUR=$(date -u +%H)
BOOT_DOW=$(date -u +%u)   # 1=월요일
echo "=== boot $(date -u -Is) hour=$BOOT_HOUR dow=$BOOT_DOW ===" | tee -a /var/log/trade-boot.log
case "$BOOT_HOUR" in
  06|07)
    # KR 마감 30분 전(15:00 KST). 평일 가드(수동 주말 부팅 방어) — 주간 주기는 주간 마커가 보장.
    if [ "$BOOT_DOW" -le 5 ]; then
      docker compose --profile trade run $(build_flag stock-trade-once "$BATCH_IMG") --rm stock-trade-once 2>&1 | tee -a /var/log/stock-trade.log
    fi
    ;;
  19|20|21)
    # US 마감 30분 전(15:30 ET; EDT 19:30/EST 20:30 UTC 발화 → hour 19·20, 21은 지연 여유).
    if [ "$BOOT_DOW" -le 5 ]; then
      docker compose --profile trade run $(build_flag us-trade-once "$BATCH_IMG") --rm us-trade-once 2>&1 | tee -a /var/log/us-trade.log
    fi
    ;;
  *)
    # 데일리(01:00 UTC=KST 10:00) + 스위퍼(02:00 UTC) → 코인만(매일, 목표 수렴형이라 재실행 무해).
    docker compose --profile trade run $(build_flag trade-once "$APP_IMG") --rm trade-once python -m trading.strategy.trade_once 2>&1 | tee -a /var/log/trade-once.log
    ;;
esac

# ── 정리 + self-stop ──
pkill -f "ssh.*tunnel@" 2>/dev/null || true
sync
sleep 3
poweroff
