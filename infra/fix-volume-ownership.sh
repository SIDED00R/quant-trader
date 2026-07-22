#!/usr/bin/env bash
# 컨테이너 non-root(uid 1000) 전환에 맞춰, 앱이 쓰는 호스트 바인드/named volume 소유권을 정렬한다.
# 멱등·self-guard: 대상이 없으면 조용히 건너뛴다(수집 VM엔 batch 볼륨·charts가 없어 사실상 no-op).
# compose up "전"에 실행해야 한다(startup·deploy 스크립트가 호출). 루트 권한 필요(VM startup=root).
#
# 파손 벡터(실측):
#  ① equity-chart의 ./charts 바인드마운트(호스트 dir이 root 소유면 uid 1000 쓰기 실패 → 차트 발행 중단)
#  ② batch 캐시 named volume 3개(라이브 VM에 이미 root 소유 파일로 차 있어 신규 chown 상속이 안 됨)
set -euo pipefail

OWNER=1000:1000
PROJECT=coin-auto-trader   # compose 프로젝트명(volume prefix) — docker-compose.yml name과 일치
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ① charts 바인드 — 호스트 dir을 미리 만들고 소유권 정렬(equity-chart가 SVG를 여기 쓴다)
mkdir -p "$REPO_DIR/charts"
chown -R "$OWNER" "$REPO_DIR/charts"

# ② batch 캐시 named volume(local 드라이버 → /var/lib/docker/volumes/<project>_<vol>/_data)
for vol in refcache cache13f insidercache; do
  data_dir="/var/lib/docker/volumes/${PROJECT}_${vol}/_data"
  if [ -d "$data_dir" ]; then
    chown -R "$OWNER" "$data_dir"
  fi
done

echo "fix-volume-ownership: done (owner=$OWNER)"
