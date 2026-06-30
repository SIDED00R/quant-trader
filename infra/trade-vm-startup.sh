#!/bin/bash
# 온디맨드 매매 VM startup — Cloud Scheduler가 매일 이 VM을 start하면:
#   docker/repo 준비(멱등) → 데이터 VM SSH 터널 → trade_once 1회 실행 → 터널 정리 → poweroff(self-stop).
# DB는 데이터 VM에서 loopback 유지, 매매 VM이 터널(-L 5432/8123)로 접속(network_mode:host).
# 터널 개인키는 /etc/tunnel_key(600), 공개키는 데이터 VM 메타데이터(tunnel 사용자)에 등록돼 있어야 한다.
# 루트로 실행. -e 제외(어떤 실패에도 마지막 poweroff까지 도달하도록).
set -uxo pipefail

DATA_VM_IP=10.128.0.2
TUNNEL_KEY=/etc/tunnel_key
REPO=/opt/coin-auto-trader

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
[ -d "$REPO" ] || git clone https://github.com/SIDED00R/coin-auto-trader.git "$REPO"
cd "$REPO" && git fetch origin main && git reset --hard origin/main
cp -n .env.example .env || true

# ── KIS 자격증명 주입 (Secret Manager kis-env → .env, VM 토큰으로 REST 접근; 실패해도 진행) ──
KIS_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "${KIS_TOKEN:-}" ]; then
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/kis-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/kis-env 2>/dev/null || true
  if [ -s /tmp/kis-env ]; then grep -v '^KIS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/kis-env > .env 2>/dev/null || true; rm -f /tmp/env.nok /tmp/kis-env; fi
fi

# ── 데이터 VM SSH 터널(loopback DB를 매매 VM 호스트로 포워딩) ──
pkill -f "ssh.*tunnel@" 2>/dev/null || true
ssh -i "$TUNNEL_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ExitOnForwardFailure=yes \
    -fN -L 5432:localhost:5432 -L 8123:localhost:8123 tunnel@"$DATA_VM_IP" </dev/null >/dev/null 2>&1
sleep 5

# ── 1회 매매(network_mode:host로 127.0.0.1 터널 접속) ──
# --build: trade-once는 코드를 이미지에 굽는다(소스 볼륨 없음). 위 git reset 한 최신 소스로 매번 재빌드해야
#          낡은 이미지의 옛 코드가 실행되는 것을 막는다(없으면 #94 결정 기록 코드가 영영 안 돌았음).
docker compose --profile trade run --build --rm trade-once python -m trading.strategy.trade_once 2>&1 | tee /var/log/trade-once.log

# ── 주식 주간 모의 리밸런싱 (월요일만; ML 이미지로 챔피언 스코어→KIS 모의주문) ──
# date -u +%u: 1=월요일. 매매 VM은 매일 01:00 UTC(=KR 10:00 장중) 기동 → 월요일에만 주식 실행 = 주 1회.
# stock-trade-once는 batch 이미지(lightgbm). KIS 자격증명은 VM .env에 있어야 함(메타데이터/시크릿 주입).
if [ "$(date -u +%u)" = "1" ]; then
  docker compose --profile trade run --build --rm stock-trade-once 2>&1 | tee /var/log/stock-trade.log
fi

# ── 정리 + self-stop ──
pkill -f "ssh.*tunnel@" 2>/dev/null || true
sync
sleep 3
poweroff
