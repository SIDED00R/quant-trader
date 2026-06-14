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

# 레포 클론 + 풀스택 기동 (public repo)
cd /opt
if [ ! -d coin-auto-trader ]; then
  git clone https://github.com/SIDED00R/coin-auto-trader.git
fi
cd coin-auto-trader
cp -n .env.example .env || true
docker compose --profile app up -d --build

echo "STARTUP_DONE"
