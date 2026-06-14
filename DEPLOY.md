# GCP 배포 설계

로컬 `docker-compose` 구성을 GCP로 이전하기 위한 설계. **이 문서는 설계만 다루며, 실제 리소스 생성/결제는 별도 단계다.**

- 생성된 프로젝트: `coin-auto-trader-jvfhgq` (프로젝트만 생성됨, 결제 미연결)
- 계정: `mywinningtime@gmail.com`

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
