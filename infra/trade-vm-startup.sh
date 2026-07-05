#!/bin/bash
# 온디맨드 매매 VM startup — Cloud Scheduler(메인 4잡 + 스위퍼 4잡, infra/setup-cicd.sh)가 start하면:
#   docker/repo 준비(멱등) → AR 이미지 pull(revision 라벨 검증, 실패 시 --build 폴백) → 로컬 DB(postgres/clickhouse) 기동+스키마
#   → UTC 부팅시각 분기로 1회 매매 → poweroff(self-stop). (자기완결 — 수집 VM과 크로스VM 터널 없음)
# 분기표(스케줄러와 정합):
#   UTC 04·05    → 데이터 유지보수(maintenance-once) ← 매월 첫 토요일 04:00 UTC + 스위퍼 05:00(월간화는 startup이 날짜 가드)
#   UTC 06·07    → KR 주식(stock-trade-once)  ← kr-close 15:00 KST + sweep 15:10 KST (06:00/06:10 UTC, 평일)
#   UTC 19·20·21 → US 주식(us-trade-once)     ← us-close 15:30 ET + sweep 15:45 ET (EDT 19:30/45·EST 20:30/45, 평일)
#   그 외(01·02) → 코인(trade-once)           ← daily 01:00 + sweep 02:00 UTC (매일)
#   (07·21은 부팅지연 여유. 임의 시각 수동 start는 코인 분기 — 목표 수렴형이라 무해)
# 스위퍼 = 존 용량 부족(ZONE_RESOURCE_POOL_EXHAUSTED) 등 기동 실패 재시도. 이중 실행은 멱등:
#   코인=목표 수렴(재실행 시 주문 0), 주식=주간 마커(week_done)가 skip.
# 알림: 잡 결과는 파이썬(common/notify_telegram)이 텔레그램 발송. 파이썬이 통보 못한 실패(exit∉{0,70})는
#   notify_fail()이 앱 이미지 CLI로 폴백 발송. 시크릿은 kis-env·telegram-env·toss-env·dart-env(Secret Manager)로 주입.
# DB는 이 VM 로컬 컨테이너(postgres/clickhouse, 127.0.0.1 loopback) — network_mode:host로 매매 컨테이너가 접속.
# pgdata/chdata 볼륨은 poweroff(stop≠delete)에도 영속. 최초 시딩은 마이그레이션 시 1회(pg_dump 복원 + maintenance_once/backfill).
# 루트로 실행. -e 제외(어떤 실패에도 마지막 poweroff까지 도달하도록).
set -uxo pipefail

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

# ── 자격증명 주입 (Secret Manager kis-env·telegram-env·toss-env·dart-env → .env, VM 토큰으로 REST 접근; 실패해도 진행) ──
# .env는 부팅 간 영속 — prefix 필터(grep -vE)로 지난 부팅의 주입 라인을 걷어내 중복 누적을 방지한다.
KIS_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "${KIS_TOKEN:-}" ]; then
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/kis-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/kis-env 2>/dev/null || true
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/telegram-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/telegram-env 2>/dev/null || true
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/toss-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/toss-env 2>/dev/null || true
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/dart-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/dart-env 2>/dev/null || true
  # 시크릿별 독립 병합 — 자기 fetch가 성공했을 때만 자기 prefix 라인을 교체(한쪽 실패가 다른 쪽을 지우지 않게)
  if [ -s /tmp/kis-env ]; then grep -v '^KIS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/kis-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/telegram-env ]; then grep -v '^TELEGRAM_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/telegram-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/toss-env ]; then grep -v '^TOSS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/toss-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/dart-env ]; then grep -v '^DART_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/dart-env > .env 2>/dev/null || true; fi
  rm -f /tmp/env.nok /tmp/kis-env /tmp/telegram-env /tmp/toss-env /tmp/dart-env
fi

# ── Artifact Registry 로그인 (gcloud 불요 — VM 메타데이터 토큰. 실패해도 진행: 아래 --build 폴백) ──
echo "${DK_TOKEN:-}" | docker login -u oauth2accesstoken --password-stdin https://us-central1-docker.pkg.dev || true

# ── 디스크 위생 (20GB 디스크 — --build 폴백이 남긴 빌드캐시·dangling 이미지가 pull을 막았던 2026-07-03 사고 재발 방지.
#    태그된 이미지는 유지해 증분 pull 보존) ──
docker builder prune -af >/dev/null 2>&1 || true
docker image prune -f >/dev/null 2>&1 || true
# 알림 폴백 CLI용 앱 이미지(0.08GB) 선확보 — batch 이미지가 깨진 날에도 실패 통보는 나가게.
docker compose --profile trade pull -q trade-once >/dev/null 2>&1 || true

# ── 로컬 DB 기동 + 스키마(자기완결 — 크로스VM 터널 없음). pgdata/chdata는 poweroff에도 영속 ──
# db-init의 depends_on(postgres/clickhouse healthy)이 로컬 DB를 먼저 띄우고 헬스 대기 후 스키마 멱등 적용.
# 시작된 postgres/clickhouse는 run 종료 후에도 유지(포트 127.0.0.1 게시) → 이후 매매 컨테이너(network_mode:host)가 접속.
docker compose up -d postgres clickhouse
docker compose run --rm db-init 2>&1 | tee -a /var/log/trade-boot.log || true

# ── 이미지 신선도 (#94/#99 불변식의 계승) ──
# CI가 org.opencontainers.image.revision=<sha> 라벨로 이미지를 굽는다. pull한 :latest의 라벨이
# 위 git reset 결과(HEAD)와 다르거나 pull이 실패하면 --build로 폴백해 최신 소스로 재빌드한다.
# 낡은 코드가 조용히 실행되는 일을 원천 차단 — **이 폴백 제거 금지**.
build_flag() {  # $1=compose 서비스명 $2=이미지 ref → ""(pull 신선) 또는 "--build"
  docker compose --profile trade pull -q "$1" >/dev/null 2>&1 || { echo "--build"; return; }
  [ "$(docker image inspect "$2" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null)" \
    = "$(git rev-parse HEAD)" ] || echo "--build"
}

# ── 잡 실패 텔레그램 폴백 (파이썬이 스스로 통보 못한 실패만: 0=성공, 70=파이썬이 이미 통보) ──
# 이미지 빌드 실패·import 즉사·OOM처럼 잡 코드가 뜨기도 전에 죽는 경우를 앱 이미지 CLI로 통보한다.
notify_fail() {  # $1=시장 라벨 $2=exit코드 $3=로그 파일
  case "$2" in 0|70) return 0 ;; esac
  MSG="🔴 [$1] 매매 잡 실패 exit=$2 ($(date -u -Is))
--- 로그 꼬리 ---
$(tail -n 20 "$3" 2>/dev/null)"
  docker compose --profile trade run --rm trade-once python -m common.notify_telegram "$MSG" >/dev/null 2>&1 || true
}

# ── 기동 시각으로 분기 (여러 스케줄러가 같은 VM을 start; 메타데이터 전달 채널 없어 UTC 시각으로 구분) ──
BOOT_HOUR=$(date -u +%H)
BOOT_DOW=$(date -u +%u)   # 1=월요일
echo "=== boot $(date -u -Is) hour=$BOOT_HOUR dow=$BOOT_DOW ===" | tee -a /var/log/trade-boot.log
case "$BOOT_HOUR" in
  04|05)
    # 데이터 유지보수(매월 첫 토요일 04:00 UTC + 스위퍼 05:00). 스케줄러는 매주 토요일 발화 —
    # 첫 주(1~7일)만 실행해 월간화한다. 토요일 새벽이라 매매 잡과 겹치지 않는다(코인은 01·02시 종료).
    if [ "$BOOT_DOW" -eq 6 ] && [ "$(date -u +%d | sed 's/^0//')" -le 7 ]; then
      docker compose --profile trade run $(build_flag maintenance-once "$BATCH_IMG") --rm maintenance-once 2>&1 | tee -a /var/log/maintenance.log
      notify_fail "데이터 유지보수" "${PIPESTATUS[0]}" /var/log/maintenance.log
    fi
    ;;
  06|07)
    # KR 마감 30분 전(15:00 KST). 평일 가드(수동 주말 부팅 방어) — 주간 주기는 주간 마커가 보장.
    if [ "$BOOT_DOW" -le 5 ]; then
      docker compose --profile trade run $(build_flag stock-trade-once "$BATCH_IMG") --rm stock-trade-once 2>&1 | tee -a /var/log/stock-trade.log
      notify_fail "KR 주식" "${PIPESTATUS[0]}" /var/log/stock-trade.log
    fi
    ;;
  19|20|21)
    # US 마감 30분 전(15:30 ET; EDT 19:30/EST 20:30 UTC 발화 → hour 19·20, 21은 지연 여유).
    if [ "$BOOT_DOW" -le 5 ]; then
      docker compose --profile trade run $(build_flag us-trade-once "$BATCH_IMG") --rm us-trade-once 2>&1 | tee -a /var/log/us-trade.log
      notify_fail "US 주식" "${PIPESTATUS[0]}" /var/log/us-trade.log
    fi
    ;;
  *)
    # 데일리(01:00 UTC=KST 10:00) + 스위퍼(02:00 UTC) → 코인만(매일, 목표 수렴형이라 재실행 무해).
    # 크립토 일봉을 로컬 CH에 REST 백필(틱 집계 대신 — 수집 VM과 디커플링). chdata 영속이라 최근분만 갱신.
    SYMS=$(grep -E '^ENSEMBLE_SYMBOLS=' .env | cut -d= -f2- 2>/dev/null); SYMS="${SYMS:-KRW-BTC,KRW-ETH}"
    docker compose --profile batch run --rm reeval python -m batch.backtest.backfill_daily --symbols "$SYMS" --days 400 2>&1 | tee -a /var/log/trade-once.log || true
    docker compose --profile trade run $(build_flag trade-once "$APP_IMG") --rm trade-once python -m trading.strategy.trade_once 2>&1 | tee -a /var/log/trade-once.log
    notify_fail "코인" "${PIPESTATUS[0]}" /var/log/trade-once.log
    ;;
esac

# ── 정리 + self-stop ──
docker image prune -f >/dev/null 2>&1 || true   # 새 pull로 방금 dangling 된 직전 :latest 회수
sync
sleep 3
poweroff
