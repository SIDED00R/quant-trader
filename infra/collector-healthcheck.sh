#!/bin/bash
# 수집 VM 헬스체크 (단일 책임: 디스크·컨테이너·틱 유입 감시 → 위반 시 텔레그램 통보 + 틱 정지 자동복구).
# /etc/cron.d(30분 주기, collector-vm-startup.sh가 등록)가 실행한다. 프로메테우스 없이 bash 최소 구성.
# 통보 폭주 방지: 검사키별 쿨다운(기본 6h, 상태파일) — 회복 시 키를 지워 재무장한다.
# 코인 틱 정지 감지 시 kafka+스트림 자동 재기동(RESTART_COOLDOWN_SEC 게이트) — 2026-07-18 kafka
# 데이터플레인 행(hang) 38시간 무복구 사고 재발 방지(얕은 컨테이너 헬스체크·unless-stopped로는 미복구).
# 텔레그램은 앱 이미지 CLI(python -m common.notify_telegram) 재사용 — .env의 TELEGRAM_*(telegram-env 주입) 필요.
set -u
cd /opt/coin-auto-trader || exit 0

DISK_MAX="${DISK_MAX:-80}"                          # 루트 디스크 사용률 상한(%)
COIN_STALE_MAX_SEC="${COIN_STALE_MAX_SEC:-1800}"    # ticks 최신 유입 허용 지연(초) — 코인은 24/7
STOCK_STALE_MAX_SEC="${STOCK_STALE_MAX_SEC:-1800}"  # stock_ticks 허용 지연(초) — KRX 장중에만 검사
COOLDOWN_SEC="${COOLDOWN_SEC:-21600}"               # 같은 검사키 재통보 간격(6h)
RESTART_COOLDOWN_SEC="${RESTART_COOLDOWN_SEC:-10800}"  # 자동 재기동 재시도 간격(3h) — 재시작 루프 방지
STATE="${STATE:-/var/tmp/collector-healthcheck.state}"
REQUIRED="kafka postgres clickhouse ingester sink stock-ingester stock-sink candle daily-aggregator"
KAFKA_CLIENTS="ingester sink candle stock-ingester stock-sink"   # kafka 의존 스트림(재기동 대상)

touch "$STATE"
NOW=$(date +%s)

send_tg() {  # $1=메시지 — 텔레그램 발송(비치명, 쿨다운 없음 — 게이트는 호출측 책임)
  docker compose --profile trade run --rm trade-once python -m common.notify_telegram \
    "🟠 [수집 VM] $1" >/dev/null 2>&1 || true
}

notify() {  # $1=검사키 $2=메시지 — 쿨다운 게이트 후 발송, 발송 시각 기록
  local key="$1" msg="$2" last
  last=$(grep "^${key} " "$STATE" 2>/dev/null | cut -d' ' -f2)
  if [ -n "$last" ] && [ $((NOW - last)) -lt "$COOLDOWN_SEC" ]; then return 0; fi
  send_tg "$msg"
  { grep -v "^${key} " "$STATE" 2>/dev/null || true; echo "$key $NOW"; } > "${STATE}.tmp"
  mv "${STATE}.tmp" "$STATE"
}

clear_key() {  # 회복 → 상태 제거(다음 위반 시 즉시 재통보)
  { grep -v "^$1 " "$STATE" 2>/dev/null || true; } > "${STATE}.tmp"
  mv "${STATE}.tmp" "$STATE"
}

restart_pipeline() {  # 코인 틱 정지 → kafka 재기동 → healthy 대기 → 스트림 재기동 (자동복구)
  local last hc l2
  last=$(grep "^ticks_restart " "$STATE" 2>/dev/null | cut -d' ' -f2)
  if [ -n "$last" ] && [ $((NOW - last)) -lt "$RESTART_COOLDOWN_SEC" ]; then return 0; fi
  # 마커를 '먼저' 기록 — 재기동이 실패/행에 빠져도 쿨다운에 산입돼 30분마다 재시작 루프가 되지 않는다.
  { grep -v "^ticks_restart " "$STATE" 2>/dev/null || true; echo "ticks_restart $NOW"; } > "${STATE}.tmp"
  mv "${STATE}.tmp" "$STATE"
  docker compose --profile collector restart kafka
  for _ in $(seq 1 24); do   # healthy 최대 120s 대기(재기동 직후 JVM 워밍업·메모리 페이지-인)
    hc=$(docker inspect -f '{{.State.Health.Status}}' kafka 2>/dev/null)
    [ "$hc" = healthy ] && break
    sleep 5
  done
  # ingester 포함 필수 — 프로듀서는 배달 실패를 삼키고 wedge될 수 있다(워치독은 2차 방어).
  # docker compose restart는 exited 컨테이너도 기동하므로 크래시 루프 중인 소비자도 커버.
  docker compose --profile collector restart $KAFKA_CLIENTS
  sleep 90
  l2=$(lag ticks)
  send_tg "코인 틱 정지 → kafka+스트림 자동 재기동 시도 (kafka=${hc:-unknown}, 90s 후 유입 지연=${l2:-측정불가}s)"
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
# 인라인 주석·후행공백 제거 필수 — compose는 벗겨서 쓰므로 grep 원문을 쓰면 AUTHENTICATION_FAILED(#236)
CHPW=$(grep -E '^CLICKHOUSE_PASSWORD=' .env 2>/dev/null | cut -d= -f2- | sed 's/[[:space:]]*#.*//; s/[[:space:]]*$//')
lag() {
  docker exec clickhouse clickhouse-client --password "${CHPW:-ch_pw}" -q \
    "SELECT toInt64(dateDiff('second', max(ingest_ts), now64(3))) FROM coin_analytics.$1" 2>/dev/null
}
L=$(lag ticks)
if [ -n "$L" ] && [ "$L" -gt "$COIN_STALE_MAX_SEC" ]; then
  notify ticks "코인 틱 유입 정지 — 최근 유입 ${L}s 전(> ${COIN_STALE_MAX_SEC}s)"
  restart_pipeline    # end-to-end 신호(CH 유입) 기준 자동복구 — CH 다운이면 L이 비어 미발동
elif [ -n "$L" ]; then
  clear_key ticks
  clear_key ticks_restart    # 회복 → 자동 재기동 가드도 재무장
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
