# GCP 배포 설계

로컬 `docker-compose` 구성을 GCP로 이전하기 위한 설계.

> **⚠️ §1~10은 매니지드 서비스(GKE·Cloud SQL·Managed Kafka) 설계안 — 미구현.** 실제 프로덕션 = **§11(단일 GCE + docker-compose, 현재 2-VM 온디맨드로 확장)**. §1~10은 향후 확장 시 참고자료다.

- 생성된 프로젝트: `coin-auto-trader-jvfhgq` (프로젝트만 생성됨, 결제 미연결)
- 계정: `mywinningtime@gmail.com`

> ## 현재 배포 상태 (2026-06-20): **앙상블 라이브 배포됨 (모의) — 2-VM 온디맨드**
> - **데이터 VM(상시)**: GCE `coin-trader-vm`(us-central1-a, **e2-medium 4GB**), `--profile data`로 **수집·저장·대시보드만**. 부팅 시 git pull. ≈$24/월. 내부IP 10.128.0.2.
> - **매매 VM(온디맨드)**: GCE `coin-trade-vm`, 평소 **정지(TERMINATED)**. **Cloud Scheduler `trade-vm-daily`가 매일 01:00 UTC(KST 10:00) 기동** → `trade_once`(SSH 터널로 데이터 VM DB 접근, 동기 매매) → 자가 종료(poweroff). 가동시간만 과금(~$1/월).
> - **공개 대시보드**: `https://jh-coinlab.duckdns.org` (Caddy 자동 HTTPS, Basic Auth).
> - **라이브 매매 경로**: `trade_once`(동기 배치). 스트리밍 `commander`/`engine`/`portfolio`는 코드로만 존재(로컬 dev). **Kafka는 데이터 팬아웃만**(매매 미사용).
> - **데이터**: ClickHouse candles_1d(BTC/ETH 2019-11~) + 전 KRW 마켓 틱 상시 수집. **모의 거래**(실거래 API 없음, 가상자본 ₩10M). 모델 출처 = `docs/model.md`.
> - **상시 비용 ~$66 → ~$25/월** (16GB 단일 → 4GB 데이터 + 온디맨드 매매로 분리).

---

## 1. 원칙

- 로컬에서 검증된 구성을 **GCP 매니지드 서비스**로 1:1 이전.
- **비용 통제 최우선**: 예산 알림, 최소 사양, 미사용 시 중지/삭제.
- 시크릿은 코드/이미지에 넣지 않고 **Secret Manager**.
- **Terraform(IaC)** 로 재현 가능하게.
- 외부 노출 최소화(API/Grafana만, 인증 적용), 데이터스토어는 사설 IP.

---

## 2. 컴포넌트 → GCP 매핑

| 로컬 | GCP (권장) | 대안 | 비고 |
|------|-----------|------|------|
| Kafka (KRaft) | **Managed Service for Apache Kafka** | Confluent Cloud / GKE self-host | 사설 IP, 최소 노드 |
| PostgreSQL | **Cloud SQL for PostgreSQL** | GCE self-host | 사설 IP, 최소 티어 |
| ClickHouse | **GCE 단일 VM(self-host)** | ClickHouse Cloud | 학습/비용상 단일 e2-small VM 권장 |
| 수집기/싱크/체결엔진/포트폴리오/릴레이 (상시 consumer) | **GKE Autopilot** | GCE + systemd | 상시 구동 long-running |
| 주문 API (FastAPI) | **Cloud Run** | GKE | 사설 리소스 접근 위해 VPC 커넥터 필요 |
| Grafana | **Grafana Cloud(무료 티어)** | GKE/GCE | 비용 절감 |
| 컨테이너 이미지 | **Artifact Registry** | — | 빌드 산출물 저장 |
| 시크릿 | **Secret Manager** | — | DB/CH/Kafka 자격증명 |
| 네트워크 | **VPC + 서브넷** | — | 사설 통신 |

> **핵심 주의**: 체결엔진은 인메모리 상태(최신가/pending/limit) + 멀티토픽 소비라 **반드시 단일 인스턴스**(GKE `replicas: 1`, HPA 미적용)로 배포한다. 릴레이는 `FOR UPDATE SKIP LOCKED`라 다중 인스턴스 가능하나 1개로 시작.

---

## 3. 네트워킹

```
                ┌──────────────── VPC (coin-vpc) ────────────────┐
 인터넷 ─▶ Cloud Run(API) ─(Serverless VPC Access)─▶            │
 인터넷 ─▶ Grafana(Cloud) ──────────────────────────▶           │
                │   GKE(consumers)  ─▶  Managed Kafka (사설IP)   │
                │                    ─▶  Cloud SQL (사설IP)       │
                │                    ─▶  ClickHouse VM (사설IP)   │
                └─────────────────────────────────────────────────┘
```

- Cloud SQL: **Private IP**(VPC 피어링), 공인 IP 비활성.
- Managed Kafka: VPC 내 사설 엔드포인트.
- ClickHouse VM: 사설 IP만, 방화벽으로 VPC 내부만 허용.
- Cloud Run → 사설 리소스 접근은 **Serverless VPC Access 커넥터** 경유.
- 외부 노출: API(Cloud Run URL, 필요시 인증), Grafana(자체 인증). 그 외 전부 사설.

---

## 4. 이미지 / 배포

- 서비스별 `Dockerfile`(또는 단일 이미지 + 엔트리포인트 분기). `python:3.13-slim` 베이스.
- 빌드 → **Artifact Registry** push (`asia-northeast3` 서울 리전 권장).
- **GKE Deployments** (consumer 5종): 각 `replicas: 1`(체결엔진은 단일 강제), `restartPolicy: Always`.
- **Cloud Run** 서비스: 주문 API. min-instances=1(콜드스타트 회피, 비용 주의) 또는 0.
- 스키마 적용(`scripts/init_db`)은 **1회성 Job**(GKE Job / Cloud Run Job)으로.
- 토픽 생성은 Managed Kafka 콘솔/Terraform 또는 init Job.

---

## 5. 시크릿 / 설정

- **Secret Manager**: `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, Kafka 자격증명.
- **Workload Identity**(GKE) / Cloud Run 서비스계정 → Secret Manager 접근 권한(`secretmanager.secretAccessor`).
- 앱 설정은 환경변수로 주입: `KAFKA_BOOTSTRAP_SERVERS`(Kafka 사설 엔드포인트), `POSTGRES_HOST`(Cloud SQL 사설 IP), `CLICKHOUSE_HOST`(VM 사설 IP) 등.
- 로컬의 `127.0.0.1` 기본값 대신 GCP 사설 IP/호스트로 오버라이드.

---

## 6. 비용 통제 (필수)

- **Cloud Billing 예산 + 알림**: 월 한도(예: ₩50,000) 설정, 50/90/100% 알림.
- 최소 사양으로 시작:
  - Cloud SQL: `db-f1-micro` 또는 `db-g1-small`
  - ClickHouse VM: `e2-small`(또는 `e2-medium`)
  - GKE: **Autopilot**(파드 단위 과금) — consumer가 가벼워 저비용
  - Managed Kafka: 최소 구성
  - Grafana: **Grafana Cloud 무료 티어**로 비용 0
- **미사용 시 중지/삭제 절차** 문서화(Terraform `destroy`, VM stop, GKE 노드 0).
- ⚠️ 대략 월 비용은 구성/사용량에 따라 크게 달라짐 — 예산 알림으로 상한을 강제할 것.

---

## 7. Terraform(IaC) 개요

```
infra/terraform/
├── main.tf            # provider, 백엔드(GCS)
├── apis.tf            # 필요한 API 활성화
├── network.tf         # VPC, 서브넷, VPC 커넥터, 사설 서비스 연결
├── artifact.tf        # Artifact Registry
├── secrets.tf         # Secret Manager 시크릿
├── cloudsql.tf        # Cloud SQL (사설 IP)
├── kafka.tf           # Managed Service for Apache Kafka + 토픽
├── clickhouse_vm.tf   # GCE VM + 방화벽
├── gke.tf             # GKE Autopilot 클러스터
├── cloudrun.tf        # Cloud Run(API) + VPC 커넥터 연결
└── variables.tf / outputs.tf
```

- 상태는 **GCS 백엔드**에 저장.
- 활성화할 API: `compute`, `container`, `sqladmin`, `managedkafka`, `run`, `artifactregistry`, `secretmanager`, `servicenetworking`, `vpcaccess`.

---

## 8. 배포 단계 (순서)

1. **결제 연결** + 위 API 활성화 + **예산 알림 설정** (가장 먼저).
2. Terraform: 네트워크 + Artifact Registry + Secret Manager.
3. 데이터스토어 프로비저닝: Cloud SQL, Managed Kafka(+토픽), ClickHouse VM.
4. 이미지 빌드 → Artifact Registry push, 스키마 적용 Job 실행.
5. consumer 5종 GKE 배포(체결엔진 replicas=1), 주문 API Cloud Run 배포.
6. Grafana(Cloud) 연결 + 데이터소스/대시보드.
7. 스모크 테스트(주문→체결→잔고, 대시보드 데이터) + 예산/모니터링 확인.

---

## 9. 로컬 ↔ GCP 차이 / 주의

- **체결엔진 단일 인스턴스 강제**(상태 인메모리) — GKE `replicas: 1`, HPA 금지.
- 시크릿 외부화(Secret Manager), 코드/이미지에 비밀번호 금지.
- `127.0.0.1` 루프백 → VPC 사설 IP/호스트로 환경변수 오버라이드.
- 업비트 WebSocket 아웃바운드 허용(Cloud NAT 등 egress 경로 필요).
- ClickHouse native(9000)/HTTP(8123)는 VPC 내부에서만 접근.
- 상시 consumer는 Cloud Run보다 GKE가 적합(장기 연결/리밸런스).

---

## 10. 다음 결정 사항 (배포 착수 전)

- 결제 계정 연결 여부/한도.
- 리전(권장: `asia-northeast3` 서울).
- Kafka: Managed Service vs Confluent Cloud.
- ClickHouse: GCE self-host vs ClickHouse Cloud.
- Grafana: Grafana Cloud(무료) vs self-host.

---

## 11. 실제 배포 (저비용 단일 VM) — 적용됨

비용 최소화를 위해 매니지드 서비스 대신 **단일 GCE VM에 docker-compose 풀스택**으로 배포했다.

- 프로젝트: `coin-auto-trader-jvfhgq`, 존: `us-central1-a` (최저가 리전)
- VM: `coin-trader-vm`, `e2-medium`(2vCPU/4GB), `pd-standard` 30GB, Ubuntu 22.04
- 부팅 스크립트(데이터 VM): [infra/gce-startup.sh](infra/gce-startup.sh) — Docker 설치 → 레포 클론 → `docker compose --profile data up -d --build` (+ 4GB 스왑). 매매 VM은 [infra/trade-vm-startup.sh](infra/trade-vm-startup.sh)(터널→trade_once→poweroff).
- 예산 알림: 결제계정에 30,000 (50/90/100%) 설정됨
- 포트는 VM 루프백에만 바인딩 → 외부 비노출, 접속은 SSH 터널

### ⚠️ 배포 함정 — 코드가 실제로 적용됐는지 반드시 확인

- **매매 VM은 코드를 이미지에 굽는다**(`trade-once`는 `build: .`, 소스 볼륨 마운트 없음). `trade-vm-startup.sh`는 부팅 때 `git reset` 후 **`docker compose run --build`** 로 매번 재빌드한다. **`--build`를 빼면** 소스만 최신이고 낡은 이미지가 계속 실행돼 새 코드가 조용히 안 돈다(에러 없이). 실제로 #94 결정 기록이 이 이유로 미동작 → #99에서 `--build` 복구. **이 플래그 제거 금지.**
- **startup-script는 VM 메타데이터에 사본으로 저장**된다. repo의 `infra/*-startup.sh`만 고치면 적용 안 됨 — 메타데이터를 갱신해야 한다:
  `gcloud compute instances add-metadata coin-trade-vm --zone us-central1-a --metadata-from-file startup-script=infra/trade-vm-startup.sh`
- **배포 검증(필수)**: 매매 VM start 후 시리얼 콘솔로 실제 실행된 코드를 확인한다(데이터 VM은 재배포 후 대시보드 탭·신규 테이블 확인).
  `gcloud compute instances get-serial-port-output coin-trade-vm --zone us-central1-a | grep trade_once`
  → `[trade_once] done — decisions=N recorded` 가 보여야 최신 코드(구버전은 `[trade_once] done` 만 찍음). 매매 안 한 날도 `decisions=N`(HOLD 포함) 기록된다.

### 접속 (SSH 터널)
```bash
gcloud compute ssh coin-trader-vm --zone=us-central1-a -- -L 3000:localhost:3000 -L 8000:localhost:8000
# 브라우저 http://localhost:3000 (admin/admin), API http://localhost:8000
```

### 웹 대시보드 (인터넷 공개 + Basic Auth)

실시간 시세·잔고·포지션·주문·체결을 한 화면에서 보는 대시보드(`/`)를 인터넷에 공개한다.
주문 가능한 API라 **반드시 Basic Auth**로 보호한다.

1. VM `.env` 에 외부 노출 + 비밀번호 설정:
   ```bash
   API_BIND=0.0.0.0
   WEB_USER=admin
   WEB_PASSWORD=<강한 비밀번호>
   ```
2. 방화벽에서 tcp:8000 허용(태그 기반):
   ```bash
   gcloud compute firewall-rules create allow-web-8000 \
     --project=coin-auto-trader-jvfhgq --network=default \
     --direction=INGRESS --action=ALLOW --rules=tcp:8000 \
     --source-ranges=0.0.0.0/0 --target-tags=coin-web
   gcloud compute instances add-tags coin-trader-vm --zone=us-central1-a --tags=coin-web
   ```
3. 재기동: `docker compose --profile data up -d`
4. 접속: `http://<VM_EXTERNAL_IP>:8000` (ID/비번 입력). 외부 IP 확인:
   ```bash
   gcloud compute instances describe coin-trader-vm --zone=us-central1-a \
     --format="value(networkInterfaces[0].accessConfigs[0].natIP)"
   ```
> 외부 IP는 기본 임시(ephemeral)라 VM stop/start 시 바뀔 수 있다. 고정하려면 정적 IP 예약(소량 과금).
> 시각은 전부 KST(Asia/Seoul) 표시(데이터는 UTC 저장).

### 고정 주소 + 자동 HTTPS (Caddy + 정적 IP + DuckDNS)

IP가 stop/start마다 바뀌고 http:8000 직접 노출은 불편/불안하므로, **정적 IP로 주소를 고정**하고
**Caddy 리버스프록시로 자동 HTTPS**(Let's Encrypt)를 적용한다. 단일 VM 구조는 그대로 유지된다.

1. **정적 IP 예약 + VM 연결**:
   ```bash
   gcloud compute addresses create coin-trader-ip --project=$PROJECT --region=us-central1
   IP=$(gcloud compute addresses describe coin-trader-ip --project=$PROJECT --region=us-central1 --format="value(address)")
   gcloud compute instances delete-access-config coin-trader-vm --zone=us-central1-a --access-config-name="external-nat" --project=$PROJECT
   gcloud compute instances add-access-config coin-trader-vm --zone=us-central1-a --access-config-name="external-nat" --address=$IP --project=$PROJECT
   ```
2. **방화벽 80/443 허용**(8000 직접 노출은 더 이상 불필요):
   ```bash
   gcloud compute firewall-rules create allow-web-https --project=$PROJECT --network=default \
     --direction=INGRESS --action=ALLOW --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=coin-web
   ```
3. **DuckDNS 도메인 연결**(무료): duckdns.org에서 서브도메인 생성 → IP를 예약한 정적 IP로 설정.
4. **VM `.env`**: `SITE_ADDRESS=<도메인>` 설정, `API_BIND` 은 비워둠(루프백), `WEB_PASSWORD` 강한 값.
5. 재기동: `docker compose --profile data up -d`. Caddy가 인증서를 자동 발급.
6. 접속: **`https://<도메인>`** (Basic Auth 입력). 인증서 발급에 80 포트로의 도달이 필요하다.

### 💰 비용 절감 — 안 쓸 때 VM 중지/삭제 (중요)
```bash
gcloud compute instances stop  coin-trader-vm --zone=us-central1-a   # 중지(디스크만 ~$2/월)
gcloud compute instances start coin-trader-vm --zone=us-central1-a   # 재시작(startup이 재기동)
gcloud compute instances delete coin-trader-vm --zone=us-central1-a  # 완전 삭제(과금 종료)
```
> e2-medium 가동 시 약 $24/월, 중지 시 디스크만 ~$2/월. 학습 후 **중지 또는 삭제** 권장.
