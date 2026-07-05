#!/bin/bash
# 수집 VM 헬스체크 (단일 책임: 디스크·컨테이너·틱 유입 감시 → 위반 시 텔레그램 통보).
# /etc/cron.d(30분 주기, collector-vm-startup.sh가 등록)가 실행한다. 프로메테우스 없이 bash 최소 구성.
# 통보 폭주 방지: 검사키별 쿨다운(기본 6h, 상태파일) — 회복 시 키를 지워 재무장한다.
# 텔레그램은 앱 이미지 CLI(python -m common.notify_telegram) 재사용 — .env의 TELEGRAM_*(telegram-env 주입) 필요.
set -u
cd /opt/coin-auto-trader || exit 0

DISK_MAX="${DISK_MAX:-80}"                          # 루트 디스크 사용률 상한(%)
COIN_STALE_MAX_SEC="${COIN_STALE_MAX_SEC:-1800}"    # ticks 최신 유입 허용 지연(초) — 코인은 24/7
STOCK_STALE_MAX_SEC="${STOCK_STALE_MAX_SEC:-1800}"  # stock_ticks 허용 지연(초) — KRX 장중에만 검사
COOLDOWN_SEC="${COOLDOWN_SEC:-21600}"               # 같은 검사키 재통보 간격(6h)
STATE="${STATE:-/var/tmp/collector-healthcheck.state}"
REQUIRED="kafka postgres clickhouse ingester sink stock-ingester stock-sink candle daily-aggregator"

touch "$STATE"
NOW=$(date +%s)

notify() {  # $1=검사키 $2=메시지 — 쿨다운 게이트 후 발송, 발송 시각 기록
  local key="$1" msg="$2" last
  last=$(grep "^${key} " "$STATE" 2>/dev/null | cut -d' ' -f2)
  if [ -n "$last" ] && [ $((NOW - last)) -lt "$COOLDOWN_SEC" ]; then return 0; fi
  docker compose --profile trade run --rm trade-once python -m common.notify_telegram \
    "🟠 [수집 VM] $msg" >/dev/null 2>&1 || true
  { grep -v "^${key} " "$STATE" 2>/dev/null || true; echo "$key $NOW"; } > "${STATE}.tmp"
  mv "${STATE}.tmp" "$STATE"
}

clear_key() {  # 회복 → 상태 제거(다음 위반 시 즉시 재통보)
  { grep -v "^$1 " "$STATE" 2>/dev/null || true; } > "${STATE}.tmp"
  mv "${STATE}.tmp" "$STATE"
}

# 1) 디스크 사용률
PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "${PCT:-0}" -ge "$DISK_MAX" ]; then
  notify disk "디스크 ${PCT}% ≥ ${DISK_MAX}% — 틱 적재 중단 위험(TTL/이미지 정리 확인)"
else
  clear_key disk
fi

# 2) 필수 수집 컨테이너 가동
UP=$(docker ps --format '{{.Names}}')
DOWN=""
for c in $REQUIRED; do echo "$UP" | grep -qx "$c" || DOWN="$DOWN $c"; done
if [ -n "$DOWN" ]; then
  notify containers "컨테이너 다운:$DOWN"
else
  clear_key containers
fi

# 3) 틱 유입 신선도 (ClickHouse max(ingest_ts) 지연) — 코인 24/7, 주식은 KRX 장중(평일 00:15~06:30 UTC)만
CHPW=$(grep -E '^CLICKHOUSE_PASSWORD=' .env 2>/dev/null | cut -d= -f2-)
lag() {
  docker exec clickhouse clickhouse-client --password "${CHPW:-ch_pw}" -q \
    "SELECT toInt64(dateDiff('second', max(ingest_ts), now64(3))) FROM coin_analytics.$1" 2>/dev/null
}
L=$(lag ticks)
if [ -n "$L" ] && [ "$L" -gt "$COIN_STALE_MAX_SEC" ]; then
  notify ticks "코인 틱 유입 정지 — 최근 유입 ${L}s 전(> ${COIN_STALE_MAX_SEC}s)"
elif [ -n "$L" ]; then
  clear_key ticks
fi
DOW=$(date -u +%u)
HM=$((10#$(date -u +%H%M)))
if [ "$DOW" -le 5 ] && [ "$HM" -ge 15 ] && [ "$HM" -le 630 ] \
   && grep -qE '^KIWOOM_APP_KEY=.+' .env 2>/dev/null; then   # 키움 키 없으면 수집 자체가 idle(정상)
  S=$(lag stock_ticks)
  if [ -n "$S" ] && [ "$S" -gt "$STOCK_STALE_MAX_SEC" ]; then
    notify stock_ticks "주식 틱 유입 정지(KRX 장중) — 최근 유입 ${S}s 전(> ${STOCK_STALE_MAX_SEC}s)"
  elif [ -n "$S" ]; then
    clear_key stock_ticks
  fi
fi

echo "$(date -u -Is) ok disk=${PCT:-NA}% down='${DOWN}' coin_lag=${L:-NA}s"
