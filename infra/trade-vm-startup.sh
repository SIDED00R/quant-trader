#!/bin/bash
# 온디맨드 매매 VM startup — Cloud Scheduler(메인 4잡 + 스위퍼 4잡, infra/setup-cicd.sh)가 start하면:
#   docker/repo 준비(멱등) → AR 이미지 pull(revision 라벨 검증, 실패 시 --build 폴백) → 로컬 DB(postgres/clickhouse) 기동+스키마
#   → UTC 부팅시각 분기로 1회 매매 → 자산 차트 발행(equity-chart → assets 브랜치) → poweroff(self-stop). (자기완결 — 수집 VM과 크로스VM 터널 없음)
# 분기표(스케줄러와 정합):
#   UTC 04·05    → 데이터 유지보수(maintenance-once) ← 매월 첫 토요일 04:00 UTC + 스위퍼 05:00(월간화는 startup이 날짜 가드)
#   UTC 06·07    → KR 주식(stock-trade-once)  ← kr-close 15:00 KST + sweep 15:10 KST (06:00/06:10 UTC, 평일)
#   UTC 19·20·21 → US 주식(us-trade-once)     ← us-close 15:00 ET + sweep 15:15 ET (EDT 19:00/15·EST 20:00/15, 평일)
#   그 외(01·02) → 코인(trade-once)           ← daily 01:00 + sweep 02:00 UTC (매일)
#   (07·21은 부팅지연 여유. 임의 시각 수동 start는 코인 분기 — 목표 수렴형이라 무해)
# 스위퍼 = 존 용량 부족(ZONE_RESOURCE_POOL_EXHAUSTED) 등 기동 실패 재시도. 이중 실행은 멱등:
#   코인=목표 수렴(재실행 시 주문 0), 주식=주간 마커(week_done)가 skip.
# 알림: 잡 결과는 파이썬(common/notify_telegram)이 텔레그램 발송. 파이썬이 통보 못한 실패(exit∉{0,70})는
#   notify_fail()이 앱 이미지 CLI로 폴백 발송. 시크릿은 kis-env·telegram-env·toss-env·dart-env·krx-env·fred-env(Secret Manager)로 주입.
# DB는 이 VM 로컬 컨테이너(postgres/clickhouse, 127.0.0.1 loopback) — network_mode:host로 매매 컨테이너가 접속.
# pgdata/chdata 볼륨은 poweroff(stop≠delete)에도 영속. 최초 시딩은 마이그레이션 시 1회(pg_dump 복원 + maintenance_once/backfill).
# 루트로 실행. -e 제외(어떤 실패에도 마지막 poweroff까지 도달하도록).
set -uxo pipefail

# ── 절대 워치독 — 어느 경로가 '행'이어도 90분 뒤 강제 종료(온디맨드 VM 무한 과금 차단) ──
# shutdown은 systemd(logind) 예약이라 startup-script 유닛이 끝나도 살아남는다(백그라운드 sleep은 reap됨).
# 정상 경로는 말미 poweroff가 먼저 실행돼 무해. 유지보수(장시간)·대시보드 분기는 아래에서 재조정.
shutdown -P +90 "trade-vm watchdog: 90분 상한 도달 — 강제 종료" || true

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

# ── 자격증명 주입 (Secret Manager kis-env·telegram-env·toss-env·dart-env·krx-env·fred-env → .env, VM 토큰으로 REST 접근; 실패해도 진행) ──
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
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/krx-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/krx-env 2>/dev/null || true
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/fred-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/fred-env 2>/dev/null || true
  curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/duckdns-env/versions/latest:access" 2>/dev/null \
    | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/duckdns-env 2>/dev/null || true
  # 시크릿별 독립 병합 — 자기 fetch가 성공했을 때만 자기 prefix 라인을 교체(한쪽 실패가 다른 쪽을 지우지 않게)
  if [ -s /tmp/kis-env ]; then grep -v '^KIS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/kis-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/telegram-env ]; then grep -v '^TELEGRAM_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/telegram-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/toss-env ]; then grep -v '^TOSS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/toss-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/dart-env ]; then grep -v '^DART_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/dart-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/krx-env ]; then grep -v '^KRX_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/krx-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/fred-env ]; then grep -v '^FRED_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/fred-env > .env 2>/dev/null || true; fi
  if [ -s /tmp/duckdns-env ]; then grep -v '^DUCKDNS_' .env > /tmp/env.nok 2>/dev/null || true; cat /tmp/env.nok /tmp/duckdns-env > .env 2>/dev/null || true; fi
  rm -f /tmp/env.nok /tmp/kis-env /tmp/telegram-env /tmp/toss-env /tmp/dart-env /tmp/krx-env /tmp/fred-env /tmp/duckdns-env
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
build_flag() {  # $1=compose 서비스명 $2=이미지 ref $3=프로파일(기본 trade) → ""(pull 신선) 또는 "--build"
  docker compose --profile "${3:-trade}" pull -q "$1" >/dev/null 2>&1 || { echo "--build"; return; }
  [ "$(docker image inspect "$2" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null)" \
    = "$(git rev-parse HEAD)" ] || echo "--build"
}

# ── 잡 실패 텔레그램 폴백 (파이썬이 스스로 통보 못한 실패만: 0=성공, 70=파이썬이 이미 통보) ──
# 이미지 빌드 실패·import 즉사·OOM처럼 잡 코드가 뜨기도 전에 죽는 경우를 앱 이미지 CLI로 통보한다.
notify_fail() {  # $1=잡 라벨 $2=exit코드 $3=로그 파일
  case "$2" in 0|70) return 0 ;; esac
  MSG="🔴 [$1] 잡 실패 exit=$2 ($(date -u -Is))
--- 로그 꼬리 ---
$(tail -n 20 "$3" 2>/dev/null)"
  docker compose --profile trade run --rm trade-once python -m common.notify_telegram "$MSG" >/dev/null 2>&1 || true
}

# ── 대시보드 모드 (gcp-cost-controller가 /start quant-vm 시 metadata vm-boot-mode=dashboard 설정) ──
# 수동 조회용: 로컬 DB는 위에서 이미 기동. api/grafana(+공개 시 caddy)만 올리고 매매·즉시 poweroff 스킵.
# 접속 2가지: ① 공개 HTTPS — web-env(SITE_ADDRESS·OAuth) 주입 시 Caddy가 https://<도메인> 자동발급·서비스(구글 OAuth 보호).
#           ② SSH 터널(공개 미설정 폴백): gcloud compute ssh coin-trade-vm -- -L 8000:localhost:8000 → http://localhost:8000
# 종료 잊음 대비 2시간 뒤 자동 poweroff(비용 상한). 정상 종료는 컨트롤러 /stop quant-vm.
BOOT_MODE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm-boot-mode" 2>/dev/null || true)
if [ "$BOOT_MODE" = "dashboard" ]; then
  # 공개 대시보드 자격증명 주입 (web-env → SITE_ADDRESS·GOOGLE_CLIENT_ID/SECRET·ALLOWED_EMAILS·SESSION_SECRET).
  # 있으면 api가 OAuth 활성(https_only 쿠키)·Caddy가 그 도메인으로 HTTPS 발급. 미설정/실패면 루프백 SSH 터널만.
  if [ -n "${KIS_TOKEN:-}" ]; then
    curl -s -H "Authorization: Bearer $KIS_TOKEN" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/web-env/versions/latest:access" 2>/dev/null \
      | python3 -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['payload']['data']).decode())" > /tmp/web-env 2>/dev/null || true
    if [ -s /tmp/web-env ]; then
      grep -vE '^(SITE_ADDRESS|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|ALLOWED_EMAILS|SESSION_SECRET)=' .env > /tmp/env.noweb 2>/dev/null || true
      cat /tmp/env.noweb /tmp/web-env > .env 2>/dev/null || true
      rm -f /tmp/env.noweb /tmp/web-env
    fi
  fi
  # 고정 IP 해제(ephemeral) 대응 — 부팅마다 바뀌는 공인 IP를 duckdns에 갱신해 공개 도메인이 이 부팅을 가리키게 한다.
  # 토큰은 .env(위 시크릿 주입)에만 있고 셸 변수엔 없으므로 grep으로 읽는다(SYMS와 동일). 미설정 시 no-op(정적 IP 유지 구성과 호환).
  DUCKDNS_TOKEN=$(grep -E '^DUCKDNS_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
  DUCKDNS_DOMAIN=$(grep -E '^DUCKDNS_DOMAIN=' .env 2>/dev/null | cut -d= -f2-)
  if [ -n "$DUCKDNS_TOKEN" ]; then
    MYIP=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" 2>/dev/null || true)
    curl -s "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN:-jh-quantlab}&token=${DUCKDNS_TOKEN}&ip=${MYIP}" >/dev/null 2>&1 || true
    echo "DUCKDNS_UPDATED ip=$MYIP" | tee -a /var/log/trade-boot.log
  fi
  # 공개 HTTPS(Caddy)는 SITE_ADDRESS·GOOGLE_CLIENT_ID 둘 다 있을 때만 — 무인증 대시보드가 공개포트에 뜨는 것 방지.
  DASH_SVCS="api grafana"
  if grep -qE '^SITE_ADDRESS=.+' .env 2>/dev/null && grep -qE '^GOOGLE_CLIENT_ID=.+' .env 2>/dev/null; then
    DASH_SVCS="$DASH_SVCS caddy"
  fi
  docker compose up -d --no-deps $DASH_SVCS 2>&1 | tee -a /var/log/trade-boot.log || true
  # 종료 잊음 대비 자동 poweroff(2h) — 비용 상한. 워치독(+90)을 취소하고 +120으로 재예약.
  # (구현 주의: `(sleep 7200; poweroff) &`는 startup 유닛 종료 시 cgroup째 reap돼 미발화 — shutdown 예약 사용)
  shutdown -c 2>/dev/null || true
  shutdown -P +120 "dashboard 모드 2h 상한" || true
  DASH_MSG="🖥️ 대시보드 기동 — SSH 터널: gcloud compute ssh coin-trade-vm -- -L 8000:localhost:8000 후 http://localhost:8000"
  case " $DASH_SVCS " in *" caddy "*) DASH_MSG="🖥️ 대시보드 기동 — https://$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2-) (구글 OAuth 로그인)" ;; esac
  docker compose --profile trade run --rm trade-once python -m common.notify_telegram \
    "$DASH_MSG (끝나면 /stop quant-vm; 미종료 시 2h 뒤 자동 종료)" >/dev/null 2>&1 || true
  echo "DASHBOARD_UP" | tee -a /var/log/trade-boot.log
  exit 0
fi

# ── 기동 시각으로 분기 (여러 스케줄러가 같은 VM을 start; 메타데이터 전달 채널 없어 UTC 시각으로 구분) ──
BOOT_HOUR=$(date -u +%H)
BOOT_DOW=$(date -u +%u)   # 1=월요일
echo "=== boot $(date -u -Is) hour=$BOOT_HOUR dow=$BOOT_DOW ===" | tee -a /var/log/trade-boot.log
case "$BOOT_HOUR" in
  04|05)
    # 데이터 유지보수(매월 첫 토요일 04:00 UTC + 스위퍼 05:00). 스케줄러는 매주 토요일 발화 —
    # 첫 주(1~7일)만 실행해 월간화한다. 토요일 새벽이라 매매 잡과 겹치지 않는다(코인은 01·02시 종료).
    if [ "$BOOT_DOW" -eq 6 ] && [ "$(date -u +%d | sed 's/^0//')" -le 7 ]; then
      # 월간 풀백필+EDGAR 수집은 90분을 넘을 수 있음 — 워치독을 6h로 재예약(행 방어는 유지)
      shutdown -c 2>/dev/null || true
      shutdown -P +360 "maintenance watchdog: 6h 상한" || true
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
    # US 마감 1시간 전(15:00 ET; EDT 19:00/EST 20:00 UTC 발화 → hour 19·20, 21은 지연 여유). 부팅 지연 흡수→주문 마감 30분 전 도달.
    if [ "$BOOT_DOW" -le 5 ]; then
      docker compose --profile trade run $(build_flag us-trade-once "$BATCH_IMG") --rm us-trade-once 2>&1 | tee -a /var/log/us-trade.log
      notify_fail "US 주식" "${PIPESTATUS[0]}" /var/log/us-trade.log
    fi
    ;;
  *)
    # 데일리(01:00 UTC=KST 10:00) + 스위퍼(02:00 UTC) → 코인만(매일, 목표 수렴형이라 재실행 무해).
    # 크립토 일봉을 로컬 CH에 REST 백필(틱 집계 대신 — 수집 VM과 디커플링). chdata 영속이라 최근분만 갱신.
    SYMS=$(grep -E '^ENSEMBLE_SYMBOLS=' .env | cut -d= -f2- 2>/dev/null); SYMS="${SYMS:-KRW-BTC,KRW-ETH}"
    # 백필 실패는 매매를 죽이지 않는다(기존 신선 데이터로 목표 수렴). 단 무언 실패(#248: tz버그로 0행 지속→신선도 위반)를
    # 조기 경보한다 — PIPESTATUS[0]=backfill exit(tee 뒤라 라인 exit는 0). `|| true` 대신 코드를 잡아 notify_fail로.
    docker compose --profile batch run $(build_flag reeval "$BATCH_IMG" batch) --rm reeval python -m batch.backtest.backfill_daily --symbols "$SYMS" --days 400 2>&1 | tee -a /var/log/trade-once.log
    notify_fail "코인 백필" "${PIPESTATUS[0]}" /var/log/trade-once.log
    # 환율(usdkrw) 일일 갱신 — 자산 곡선 '전체(KRW 환산)'용(월간 유지보수만으론 최대 한 달 지연).
    # 실패해도 매매·차트는 진행(직전 환율 forward-fill 캐리) — 실패 통보의 정본은 월간 _fred_step.
    docker compose --profile trade run $(build_flag maintenance-once "$BATCH_IMG") --rm maintenance-once python -m batch.data.fred 2>&1 | tee -a /var/log/trade-once.log || true
    docker compose --profile trade run $(build_flag trade-once "$APP_IMG") --rm trade-once python -m trading.strategy.trade_once 2>&1 | tee -a /var/log/trade-once.log
    notify_fail "코인" "${PIPESTATUS[0]}" /var/log/trade-once.log
    ;;
esac

# ── 자산 차트 갱신 + assets 브랜치 발행 (비치명 — 어떤 실패도 poweroff를 막지 않음) ──
# equity_snapshots → SVG(라이트/다크) 렌더 후 orphan 단일 커밋을 assets 브랜치에 force-push한다.
# README <picture>가 raw.githubusercontent…/assets/…를 참조. force-push 단일 커밋 = 브랜치 크기
# SVG 2개 고정(히스토리 무증가), deploy.yml은 main 전용이라 CI 미발화. 쓰기 배포키(github-push-key)는
# 읽기 키(github-deploy-key)와 분리 — 키/시크릿 미준비면 push만 조용히 스킵(코드 배포와 디커플링).
docker compose --profile trade run $(build_flag equity-chart "$APP_IMG") --rm equity-chart 2>&1 | tee -a /var/log/equity-chart.log || true
PUSH_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
curl -s -H "Authorization: Bearer ${PUSH_TOKEN:-}" "https://secretmanager.googleapis.com/v1/projects/coin-auto-trader-jvfhgq/secrets/github-push-key/versions/latest:access" 2>/dev/null \
  | python3 -c "import sys,json,base64;sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)['payload']['data']))" > /root/.ssh/id_ed25519_push 2>/dev/null || true
chmod 600 /root/.ssh/id_ed25519_push 2>/dev/null || true
if [ -s charts/equity-light.svg ] && [ -s /root/.ssh/id_ed25519_push ]; then
  PUSH_TMP=$(mktemp -d)
  git -C "$PUSH_TMP" init -q -b assets \
    && cp charts/equity-*.svg "$PUSH_TMP"/ \
    && git -C "$PUSH_TMP" -c user.name=trade-vm -c user.email=trade-vm@quant-trader.local add -A \
    && git -C "$PUSH_TMP" -c user.name=trade-vm -c user.email=trade-vm@quant-trader.local commit -qm "equity charts $(date -u -Is)" \
    && GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519_push -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" \
       git -C "$PUSH_TMP" push -qf git@github.com:SIDED00R/quant-trader.git assets:assets \
    && echo "EQUITY_CHART_PUBLISHED" | tee -a /var/log/equity-chart.log \
    || echo "EQUITY_CHART_PUSH_FAILED(비치명)" | tee -a /var/log/equity-chart.log
  rm -rf "$PUSH_TMP"
fi
rm -f /root/.ssh/id_ed25519_push 2>/dev/null || true   # 쓰기 키는 push 스텝 동안만 디스크에 존재(유출 반경 최소화)

# ── 정리 + self-stop ──
docker image prune -f >/dev/null 2>&1 || true   # 새 pull로 방금 dangling 된 직전 :latest 회수
sync
sleep 3
poweroff
