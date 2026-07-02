#!/bin/bash
# 데이터 VM 배포 스크립트 — CI(deploy-data-vm 잡)가 SSH로 실행한다.
# 호출 측(.github/workflows/deploy.yml)이 git reset --hard origin/main을 먼저 수행하므로
# 이 파일 자체도 항상 최신본으로 실행된다. 수동 배포에도 그대로 사용 가능:
#   sudo bash /opt/coin-auto-trader/infra/deploy-data-vm.sh [기대 sha]
set -euxo pipefail
SHA=${1:-}
APP_IMG=us-central1-docker.pkg.dev/coin-auto-trader-jvfhgq/docker/quant-trader-app:latest
cd /opt/coin-auto-trader

# Artifact Registry 로그인 (gcloud 불요 — VM 메타데이터 토큰)
curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])" \
  | docker login -u oauth2accesstoken --password-stdin https://us-central1-docker.pkg.dev

# 프리빌드 이미지 pull(빠름) — 실패 시 로컬 빌드 폴백
if docker compose --profile data pull -q; then B=""; else B="--build"; fi
docker compose --profile data up -d $B --remove-orphans

# pull 경로면 배포 sha 검증 — 불일치는 '조용한 구버전'이므로 실패로 크게 알린다
if [ -n "$SHA" ] && [ -z "$B" ]; then
  GOT=$(docker image inspect "$APP_IMG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  if [ "$GOT" != "$SHA" ]; then echo "이미지 revision 불일치: $GOT != $SHA"; exit 1; fi
fi

docker image prune -f
echo "DEPLOY_DONE sha=${SHA:-none} mode=${B:-pull}"
