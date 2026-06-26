#!/bin/bash
# GCE VM 부팅 시 1회 실행: Docker 설치 + 레포 클론 + 풀스택 기동.
set -euxo pipefail

# 4GB RAM에서 OOM 방지용 스왑
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git

# Docker 설치
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 레포 클론/갱신 + 풀스택 기동 (public repo)
cd /opt
if [ ! -d coin-auto-trader ]; then
  git clone https://github.com/SIDED00R/coin-auto-trader.git
fi
cd coin-auto-trader
git fetch origin main && git reset --hard origin/main   # 부팅 시 최신 main 반영(.env는 gitignore라 보존)
cp -n .env.example .env || true
# 데이터 VM = 수집·저장·대시보드 서브셋만 상시(2-VM 분리). 매매는 온디맨드 매매 VM의 trade_once 담당.
# 이전 세션 컨테이너 전체 정리 후 data 서브셋만 기동(restart:unless-stopped 매매 컨테이너의 부팅 자동재시작 방지).
# down은 명시한 프로파일 컨테이너만 내리므로 매매(app)·배치까지 전부 나열해야 함(미명시 시 app 컨테이너 잔존).
docker compose --profile app --profile data --profile batch --profile trade down --remove-orphans || true  # 볼륨 보존
docker compose --profile data up -d --build             # db-init(스키마, candles_1d 포함)은 의존성으로 자동 실행

# 일봉(candles_1d)은 디스크 볼륨에 영속 → 최초 1회만 백필 필요(부팅마다 X). 미적재 시:
#   docker compose run --rm commander python -m batch.backtest.backfill_daily --symbols KRW-BTC,KRW-ETH --days 2200
echo "STARTUP_DONE"
