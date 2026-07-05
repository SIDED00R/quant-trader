#!/bin/bash
# CI/CD·스케줄러 1회 셋업 (멱등 — 재실행 안전). 소유자 gcloud 자격증명으로 로컬에서 실행:
#   bash infra/setup-cicd.sh
# 구성: API 활성화 → Artifact Registry(+정리정책) → WIF(github-pool/provider) → 배포 SA+최소권한
#       → VM SA에 AR reader → 텔레그램 VM봇 SA에 인스턴스제어 → 모니터링(이메일 채널+기동실패 알림)
#       → 매매 스케줄러 8잡 upsert → 매매·수집 VM 메타데이터 반영(수집 VM은 최초 1회 e2-small 축소).
set -euo pipefail

P=coin-auto-trader-jvfhgq
Z=us-central1-a
R=us-central1
GH=SIDED00R/quant-trader
EMAIL=mywinningtime@gmail.com
PN=$(gcloud projects describe $P --format='value(projectNumber)')
DEPLOYER=github-deployer@$P.iam.gserviceaccount.com
VMSA=${PN}-compute@developer.gserviceaccount.com
URI=https://compute.googleapis.com/compute/v1/projects/$P/zones/$Z/instances/coin-trade-vm/start

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

echo "── 5b. 텔레그램 VM 봇(gen2 함수 런타임 SA=기본 compute SA)에 인스턴스 start/stop 권한"
gcloud projects add-iam-policy-binding $P \
  --member=serviceAccount:$VMSA --role=roles/compute.instanceAdmin.v1 --quiet

echo "── 6. 사전 점검 (스코프에 cloud-platform 없으면 set-service-account 필요, OS Login은 꺼져 있어야 함)"
gcloud compute instances describe coin-trader-vm --zone=$Z --project $P --format='value(serviceAccounts[0].scopes)' || true
gcloud compute instances describe coin-trade-vm --zone=$Z --project $P --format='value(serviceAccounts[0].scopes)' || true
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
upsert trade-vm-us-close       "30 15 * * 1-5" "America/New_York"   # US 마감 30분 전(DST 자동)
upsert trade-vm-us-close-sweep "45 15 * * 1-5" "America/New_York"
upsert trade-vm-maintenance       "0 4 * * 6"     "Etc/UTC"            # 데이터 유지보수(매주 토 발화, 첫 주 가드는 startup이 담당)
upsert trade-vm-maintenance-sweep "0 5 * * 6"     "Etc/UTC"

echo "── 9. 매매 VM 메타데이터 즉시 반영 (새 KR 잡이 구 분기표로 발화하는 일 방지)"
gcloud compute instances add-metadata coin-trade-vm --project $P --zone=$Z \
  --metadata-from-file startup-script="$(dirname "$0")/trade-vm-startup.sh" --quiet

echo "── 10. 수집 VM(coin-trader-vm) 메타데이터(collector startup) + e2-small 축소(최초 1회만 stop/resize/start; 이미 small이면 no-op)"
gcloud compute instances add-metadata coin-trader-vm --project $P --zone=$Z \
  --metadata-from-file startup-script="$(dirname "$0")/collector-vm-startup.sh" --quiet
CUR_MT=$(gcloud compute instances describe coin-trader-vm --project $P --zone=$Z --format='value(machineType.scope(machineTypes))' 2>/dev/null || true)
if [ -n "$CUR_MT" ] && [ "$CUR_MT" != "e2-small" ]; then
  echo "  머신타입 $CUR_MT → e2-small (stop→resize→start; 틱 수집 잠시 중단)"
  gcloud compute instances stop coin-trader-vm --project $P --zone=$Z --quiet
  gcloud compute instances set-machine-type coin-trader-vm --project $P --zone=$Z --machine-type=e2-small --quiet
  gcloud compute instances start coin-trader-vm --project $P --zone=$Z --quiet
fi

echo "SETUP_DONE"
