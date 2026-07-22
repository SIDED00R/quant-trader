#!/bin/bash
# 틱 수집 전용 VM(coin-trader-vm 재활용) startup — 부팅 시 1회: Docker 설치 + 레포 클론 + collector 프로파일 기동.
# collector = Kafka + ClickHouse + Postgres(스키마용) + WS 레코더(업비트·키움 틱 → 집계 → CH)만.
# 대시보드(api/caddy/grafana)·매매는 제외(온디맨드 매매 VM 담당) → e2-small로 축소해 상시 비용 절감.
# 24/7 틱 아카이브(ticks/stock_ticks/candles_1m/1d)를 로컬 chdata 볼륨에 영속 적재한다.
set -euxo pipefail

# 2GB RAM(e2-small)에서 OOM 방지용 스왑
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

# 레포 클론/갱신 (private repo — Secret Manager deploy key로 SSH fetch)
mkdir -p /root/.ssh && chmod 700 /root/.ssh
DK_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])" 2>/dev/null || true)
curl -s -H "Authorization: Bearer $DK_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/github-deploy-key/versions/latest:access" 2>/dev/null \
  | python3 -c "import sys,json,base64;sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)['payload']['data']))" > /root/.ssh/id_ed25519 2>/dev/null || true
chmod 600 /root/.ssh/id_ed25519 2>/dev/null || true
export GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
cd /opt
if [ ! -d coin-auto-trader ]; then
  git clone git@github.com:SIDED00R/quant-trader.git coin-auto-trader   # 디렉터리명은 기존 경로 유지(.env 등 보존)
fi
cd coin-auto-trader
git remote set-url origin git@github.com:SIDED00R/quant-trader.git   # https(익명) → SSH deploy key(private repo)
git fetch origin main && git reset --hard origin/main   # 부팅 시 최신 main 반영(.env는 gitignore라 보존)
cp -n .env.example .env || true

# ── 자격증명 주입 (Secret Manager kis-env → .env; 키움 WS 인증 등 레코더가 쓰던 값 보존; 실패해도 진행) ──
KIS_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "${KIS_TOKEN:-}" ]; then
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/kis-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/kis-env 2>/dev/null || true
  if [ -s /tmp/kis-env ]; then grep -v '^KIS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/kis-env > .env 2>/dev/null || true; rm -f /tmp/env.nok /tmp/kis-env; fi
fi
# ── telegram-env 주입 (kis-env와 동일 패턴) — 헬스체크(collector-healthcheck.sh) 텔레그램 통보용.
#    VM SA에 telegram-env secretAccessor 1회 바인딩 필요(DEPLOY.md). 실패해도 진행(통보만 스킵됨).
if [ -n "${KIS_TOKEN:-}" ]; then
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/telegram-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/tg-env 2>/dev/null || true
  if [ -s /tmp/tg-env ]; then grep -v '^TELEGRAM_' .env > /tmp/env.notg 2>/dev/null || true; cat /tmp/env.notg /tmp/tg-env > .env 2>/dev/null || true; rm -f /tmp/env.notg /tmp/tg-env; fi
fi
# ── toss-env 주입 (telegram-env와 동일 패턴) — /차트 봇(telegram-bot)의 온디맨드 일봉 fetch용.
#    VM SA에 toss-env secretAccessor 1회 바인딩 필요(DEPLOY.md). 실패해도 진행(봇은 토큰 없으면 '조회 실패' 응답).
if [ -n "${KIS_TOKEN:-}" ]; then
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/toss-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/toss-env 2>/dev/null || true
  if [ -s /tmp/toss-env ]; then grep -v '^TOSS_' .env > /tmp/env.notoss 2>/dev/null || true; cat /tmp/env.notoss /tmp/toss-env > .env 2>/dev/null || true; rm -f /tmp/env.notoss /tmp/toss-env; fi
fi

# ── Artifact Registry 로그인 (CI 프리빌드 이미지 pull용 — 실패 시 아래 --build 폴백) ──
echo "${DK_TOKEN:-}" | docker login -u oauth2accesstoken --password-stdin https://us-central1-docker.pkg.dev || true

# 수집 전용 서브셋만 상시 기동. 이전 세션 컨테이너 전체 정리 후 collector 서브셋만(매매/대시보드 컨테이너 부팅 자동재시작 방지).
# down은 명시한 프로파일만 내리므로 app·data·batch·trade·collector 전부 나열.
docker compose --profile app --profile data --profile batch --profile trade --profile collector down --remove-orphans || true  # 볼륨(chdata 등) 보존
# CI 프리빌드 이미지 pull(빠름) — 실패 시 로컬 빌드 폴백(#94/#99 불변식 계승 — 폴백 제거 금지)
if docker compose --profile collector pull -q; then B=""; else B="--build"; fi
bash infra/fix-volume-ownership.sh                     # non-root 볼륨 소유권 정렬(up 전, 멱등)
docker compose --profile collector up -d $B            # db-init(스키마)은 의존성으로 자동 실행

# ── 헬스체크 cron 등록 (30분 주기: 디스크·컨테이너·틱 유입 → 텔레그램) + 주간 도커 정리(빌드캐시·dangling) ──
# 스택 기동 뒤에 등록 — 부팅 중(down~up 사이) cron 틱이 '컨테이너 다운' 오탐을 내지 않게.
printf '*/30 * * * * root bash /opt/coin-auto-trader/infra/collector-healthcheck.sh >> /var/log/collector-healthcheck.log 2>&1\n0 3 * * 0 root docker builder prune -af >/dev/null 2>&1; docker image prune -f >/dev/null 2>&1\n' \
  > /etc/cron.d/collector-healthcheck
chmod 644 /etc/cron.d/collector-healthcheck

# 틱 아카이브의 candles_1d는 틱 집계로 채워진다(가동 이후부터). 과거 코인 일봉 시딩이 필요하면(1회):
#   docker compose --profile batch run --rm reeval python -m batch.candles.backfill_daily --symbols KRW-BTC,KRW-ETH --days 2200
echo "STARTUP_DONE"
