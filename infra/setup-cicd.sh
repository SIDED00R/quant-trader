#!/bin/bash
# CI/CD·스케줄러 1회 셋업 (멱등 — 재실행 안전). 소유자 gcloud 자격증명으로 로컬에서 실행:
#   bash infra/setup-cicd.sh
# 구성: API 활성화 → Artifact Registry(+정리정책) → WIF(github-pool/provider) → 배포 SA+최소권한
#       → VM SA에 AR reader → 모니터링(이메일 채널+기동실패 알림)
#       → 매매 스케줄러 8잡 upsert → 매매·수집 VM 메타데이터 반영(수집 VM은 최초 1회 e2-small 축소).
# 참고: 매매 VM on/off·대시보드 모드는 별도 프로젝트(gcp-cost-controller)가 cross-project로 제어(여기선 IAM 부여 안 함).
set -euo pipefail

P=coin-auto-trader-jvfhgq
Z=us-central1-a          # 수집 VM(coin-trader-vm, 상시) 존
TRADE_Z=us-central1-c    # 매매 VM(coin-trade-vm) 존 — us-central1-a 용량 고갈 회피로 이전(#266)
R=us-central1
GH=SIDED00R/quant-trader
EMAIL=mywinningtime@gmail.com
PN=$(gcloud projects describe $P --format='value(projectNumber)')
DEPLOYER=github-deployer@$P.iam.gserviceaccount.com
VMSA=${PN}-compute@developer.gserviceaccount.com
URI=https://compute.googleapis.com/compute/v1/projects/$P/zones/$TRADE_Z/instances/coin-trade-vm/start

echo "── 1. API 활성화"
gcloud services enable artifactregistry.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudscheduler.googleapis.com monitoring.googleapis.com logging.googleapis.com --project $P --quiet

echo "── 2. Artifact Registry(docker) + 정리정책(30일 초과 삭제, 최근 10개 유지)"
gcloud artifacts repositories describe docker --location=$R --project $P >/dev/null 2>&1 \
  || gcloud artifacts repositories create docker --repository-format=docker --location=$R --project $P --quiet
cat > /tmp/ar-cleanup.json <<'EOF'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 10}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "2592000s"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies docker --location=$R --project $P \
  --policy=/tmp/ar-cleanup.json --no-dry-run --quiet

echo "── 3. Workload Identity Federation (레포 $GH 핀)"
gcloud iam workload-identity-pools describe github-pool --location=global --project $P >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create github-pool --location=global --project $P \
       --display-name="GitHub Actions" --quiet
gcloud iam workload-identity-pools providers describe github-provider \
  --workload-identity-pool=github-pool --location=global --project $P >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc github-provider \
       --workload-identity-pool=github-pool --location=global --project $P \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
       --attribute-condition="assertion.repository=='$GH'" --quiet

echo "── 4. 배포 SA + 최소권한"
gcloud iam service-accounts describe $DEPLOYER --project $P >/dev/null 2>&1 \
  || gcloud iam service-accounts create github-deployer --project $P --display-name="GitHub Actions deployer" --quiet
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER --project $P \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PN/locations/global/workloadIdentityPools/github-pool/attribute.repository/$GH" --quiet
gcloud artifacts repositories add-iam-policy-binding docker --location=$R --project $P \
  --member=serviceAccount:$DEPLOYER --role=roles/artifactregistry.writer --quiet
gcloud projects add-iam-policy-binding $P \
  --member=serviceAccount:$DEPLOYER --role=roles/compute.instanceAdmin.v1 --quiet
gcloud iam service-accounts add-iam-policy-binding $VMSA --project $P \
  --member=serviceAccount:$DEPLOYER --role=roles/iam.serviceAccountUser --quiet   # VM(해당 SA) setMetadata의 actAs

echo "── 5. VM SA에 AR reader (양 VM 이미지 pull)"
gcloud artifacts repositories add-iam-policy-binding docker --location=$R --project $P \
  --member=serviceAccount:$VMSA --role=roles/artifactregistry.reader --quiet

echo "── 6. 사전 점검 (스코프에 cloud-platform 없으면 set-service-account 필요, OS Login은 꺼져 있어야 함)"
gcloud compute instances describe coin-trader-vm --zone=$Z --project $P --format='value(serviceAccounts[0].scopes)' || true
gcloud compute instances describe coin-trade-vm --zone=$TRADE_Z --project $P --format='value(serviceAccounts[0].scopes)' || true
gcloud compute project-info describe --project $P --format='value(commonInstanceMetadata.items)' | grep -i oslogin || echo "(project OS Login 미설정 = OK)"

echo "── 7. 모니터링 — 이메일 채널 + VM 기동실패(instances.start ERROR) 알림"
CH=$(gcloud beta monitoring channels list --project $P --filter="labels.email_address='$EMAIL'" --format='value(name)' | head -1)
if [ -z "$CH" ]; then
  CH=$(gcloud beta monitoring channels create --project $P --display-name="ops email" \
        --type=email --channel-labels=email_address=$EMAIL --format='value(name)' --quiet)
fi
if ! gcloud alpha monitoring policies list --project $P --format='value(displayName)' 2>/dev/null | grep -q "^trade-vm start 실패$"; then
  cat > /tmp/vm-start-fail-policy.json <<EOF
{
  "displayName": "trade-vm start 실패",
  "combiner": "OR",
  "conditions": [{
    "displayName": "instances.start ERROR (존 용량 부족 등)",
    "conditionMatchedLog": {
      "filter": "resource.type=\"gce_instance\" AND protoPayload.methodName:\"instances.start\" AND severity>=ERROR"
    }
  }],
  "alertStrategy": {
    "notificationRateLimit": {"period": "3600s"},
    "autoClose": "604800s"
  },
  "notificationChannels": ["$CH"]
}
EOF
  gcloud alpha monitoring policies create --project $P --policy-from-file=/tmp/vm-start-fail-policy.json --quiet
fi

echo "── 8. 매매 스케줄러 8잡 upsert (메인 4 + 스위퍼 4 — 스위퍼=기동실패 재시도, 이중실행은 멱등)"
upsert() {  # $1=잡 $2=cron $3=tz
  gcloud scheduler jobs update http "$1" --project $P --location=$R --schedule="$2" --time-zone="$3" \
    --uri="$URI" --http-method=POST --oauth-service-account-email="$VMSA" --quiet 2>/dev/null \
  || gcloud scheduler jobs create http "$1" --project $P --location=$R --schedule="$2" --time-zone="$3" \
    --uri="$URI" --http-method=POST --oauth-service-account-email="$VMSA" --quiet
}
upsert trade-vm-daily          "0 1 * * *"     "Etc/UTC"            # 코인 (KST 10:00)
upsert trade-vm-daily-sweep    "0 2 * * *"     "Etc/UTC"
upsert trade-vm-kr-close       "0 15 * * 1-5"  "Asia/Seoul"         # KR 마감 30분 전
upsert trade-vm-kr-close-sweep "10 15 * * 1-5" "Asia/Seoul"
upsert trade-vm-us-close       "0 15 * * 1-5"  "America/New_York"   # US 마감 1시간 전(15:00 ET — 부팅 지연 ~30분 흡수→주문 마감 30분 전 도달; DST 자동)
upsert trade-vm-us-close-sweep "15 15 * * 1-5" "America/New_York"
upsert trade-vm-maintenance       "0 4 * * 6"     "Etc/UTC"            # 데이터 유지보수(매주 토 발화, 첫 주 가드는 startup이 담당)
upsert trade-vm-maintenance-sweep "0 5 * * 6"     "Etc/UTC"

echo "── 9. 매매 VM 메타데이터 즉시 반영 (새 KR 잡이 구 분기표로 발화하는 일 방지)"
gcloud compute instances add-metadata coin-trade-vm --project $P --zone=$TRADE_Z \
  --metadata-from-file startup-script="$(dirname "$0")/trade-vm-startup.sh" --quiet
# 머신타입 보장: us-central1-a 존 용량 고갈(ZONE_RESOURCE_POOL_EXHAUSTED)로 start가 반복 실패(#260/#261 — 07-16 US 매매 누락,
# 07-17엔 KR 시간대까지 확산 + 마지막 보루 c2d-standard-2마저 고갈). 원인은 특정 머신타입이 아니라 붐비는 us-central1-a 존
# 자체 → 매매 VM을 us-central1-c($TRADE_Z)로 이전(#266)하고 머신타입을 최저가 범용 e2-standard-2(동일 2vCPU/8GB)로 되돌린다.
# 온디맨드 VM이라 평소 TERMINATED — set-machine-type만 적용하고 start는 하지 않는다(스케줄러가 기동; 켜두면 스케줄 매매가 막힘).
TRADE_MT=$(gcloud compute instances describe coin-trade-vm --project $P --zone=$TRADE_Z --format='value(machineType.scope(machineTypes))' 2>/dev/null || true)
if [ -n "$TRADE_MT" ] && [ "$TRADE_MT" != "e2-standard-2" ]; then
  echo "  매매 VM 머신타입 $TRADE_MT → e2-standard-2 ($TRADE_Z)"
  TRADE_STATUS=$(gcloud compute instances describe coin-trade-vm --project $P --zone=$TRADE_Z --format='value(status)' 2>/dev/null || true)
  [ "$TRADE_STATUS" = "RUNNING" ] && gcloud compute instances stop coin-trade-vm --project $P --zone=$TRADE_Z --quiet
  gcloud compute instances set-machine-type coin-trade-vm --project $P --zone=$TRADE_Z --machine-type=e2-standard-2 --quiet
fi

echo "── 10. 수집 VM(coin-trader-vm) 메타데이터(collector startup) + e2-small 축소(최초 1회만 stop/resize/start; 이미 small이면 no-op)"
gcloud compute instances add-metadata coin-trader-vm --project $P --zone=$Z \
  --metadata-from-file startup-script="$(dirname "$0")/collector-vm-startup.sh" --quiet
CUR_MT=$(gcloud compute instances describe coin-trader-vm --project $P --zone=$Z --format='value(machineType.scope(machineTypes))' 2>/dev/null || true)
if [ -n "$CUR_MT" ] && [ "$CUR_MT" != "e2-small" ]; then
  echo "  머신타입 $CUR_MT → e2-small (stop→resize→start; 틱 수집 잠시 중단)"
  gcloud compute instances stop coin-trader-vm --project $P --zone=$Z --quiet
  gcloud compute instances set-machine-type coin-trader-vm --project $P --zone=$Z --machine-type=e2-small --quiet
  # start는 재시도로 감싼다(set -e 하에서 실패 시 상시 VM이 정지 방치되지 않게 — ZONE 용량 부족 대비)
  started=""
  for i in 1 2 3; do
    if gcloud compute instances start coin-trader-vm --project $P --zone=$Z --quiet; then started=1; break; fi
    echo "  수집 VM start 재시도 $i (ZONE 용량 등)"; sleep 20
  done
  [ -n "$started" ] || echo "🔴 경고: 수집 VM이 정지된 채 남았습니다 — 'gcloud compute instances start coin-trader-vm --zone=$Z' 로 수동 기동 필요(틱 수집 중단 중)."
fi

echo "SETUP_DONE"
