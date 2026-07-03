# GCP 배포 설계

로컬 `docker-compose` 구성을 GCP로 이전하기 위한 설계.

> **⚠️ §1~10은 매니지드 서비스(GKE·Cloud SQL·Managed Kafka) 설계안 — 미구현.** 실제 프로덕션 = **§11(단일 GCE + docker-compose, 현재 2-VM 온디맨드로 확장)**. §1~10은 향후 확장 시 참고자료다.

- 생성된 프로젝트: `coin-auto-trader-jvfhgq` (프로젝트만 생성됨, 결제 미연결)
- 계정: `mywinningtime@gmail.com`

> ## 현재 배포 상태 (2026-07-02): **라이브 배포됨 (모의) — 코인+주식(KR/US) 2-VM 온디맨드 + CI/CD 자동배포**
> - **데이터 VM(상시)**: GCE `coin-trader-vm`(us-central1-a, **e2-medium 4GB**), `--profile data`로 **수집·저장·대시보드만**. 부팅 시 git pull + AR 이미지 pull. ≈$24/월. 내부IP 10.128.0.2.
> - **CI/CD**: main 머지 → GitHub Actions([.github/workflows/deploy.yml](.github/workflows/deploy.yml))가 이미지 병렬 빌드(레지스트리 캐시)→Artifact Registry 푸시→데이터 VM 배포+매매 VM 메타데이터 갱신→`/healthz` sha 검증. 1회 셋업 = [infra/setup-cicd.sh](infra/setup-cicd.sh).
> - **매매 VM(온디맨드)**: GCE `coin-trade-vm`, 평소 **정지(TERMINATED)**. Cloud Scheduler **6잡(메인 3 + 스위퍼 3)** 이 같은 VM을 `instances/start` 기동(startup이 UTC 부팅시각으로 분기):
>   - **코인**: `trade-vm-daily` 매일 01:00 UTC(KST 10:00) + 스위퍼 02:00 UTC → `trade_once` → poweroff.
>   - **KR 주식**: `trade-vm-kr-close` 평일 15:00 KST(마감 30분 전) + 스위퍼 15:10 KST → `stock_trade_once`.
>   - **US 주식**: `trade-vm-us-close` 평일 15:30 ET(마감 30분 전, DST 자동) + 스위퍼 15:45 ET → `us_trade_once`.
>   - **스위퍼 = 기동실패 재시도**: 존 용량 부족(`ZONE_RESOURCE_POOL_EXHAUSTED`, 실사례 2026-06-30~07-01 3연속) 시 수 분 뒤 재기동. 이중 실행은 멱등(코인=목표 수렴, 주식=주간 마커). 기동 실패는 이메일 알림(아래 §11).
>   - **주 1회 멱등(휴장·실패 재시도)**: 주식은 평일마다 부팅·시도하지만 `weekly_rebalance` 마커가 **주 1회만 실제 매매**를 보장. 휴장일(US=NYSE 게이트, KR=체결기반)·일시적 실패면 그 주 마커를 남기지 않아 **다음 평일 자동 재시도**. 체결 자체는 `kis_chase`(미체결 취소·재주문 추격)가 보장.
>   - SSH 터널로 데이터 VM DB 접근, 동기 매매. 가동시간만 과금(~$1/월).
> - **공개 대시보드**: `https://jh-quantlab.duckdns.org` (Caddy 자동 HTTPS, Basic Auth).
> - **라이브 매매 경로**: `trade_once`(동기 배치). 스트리밍 `commander`/`engine`/`portfolio`는 코드로만 존재(로컬 dev). **Kafka는 데이터 팬아웃만**(매매 미사용).
> - **데이터**: ClickHouse candles_1d(BTC/ETH 2019-11~) + 전 KRW 마켓 틱 상시 수집. **모의 거래**(실거래 API 없음) — 코인 가상잔고 ₩10M, 주식 KIS 모의계좌(KR ₩10M·US $100k). 모델 출처 = `docs/model.md`.
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
- 부팅 스크립트(데이터 VM): [infra/gce-startup.sh](infra/gce-startup.sh) — Docker 설치 → 레포 클론 → AR 이미지 pull(실패 시 `--build` 폴백) → `docker compose --profile data up -d` (+ 4GB 스왑). 매매 VM은 [infra/trade-vm-startup.sh](infra/trade-vm-startup.sh)(터널→부팅시각 분기 매매→poweroff).
- 컨테이너 이미지: Artifact Registry `us-central1/docker`의 `quant-trader-app`/`quant-trader-batch` — CI가 빌드·푸시(`:latest`+`:<sha>`+`:buildcache`), VM은 pull.
- 예산 알림: 결제계정에 30,000 (50/90/100%) 설정됨
- 포트는 VM 루프백에만 바인딩 → 외부 비노출, 접속은 SSH 터널

### ⚠️ 배포 함정 — 코드가 실제로 적용됐는지 반드시 확인

- **매매 VM은 코드를 이미지에 굽는다**(소스 볼륨 마운트 없음). 부팅 시 `git reset` 후 AR `:latest`를 pull하고 **이미지의 `org.opencontainers.image.revision` 라벨 vs `git rev-parse HEAD`를 검증** — 불일치·pull 실패면 `--build`로 최신 소스 재빌드(`build_flag()` 헬퍼). 예전엔 무조건 `--build`였고(#94 결정 기록 미동작 → #99 복구), 지금은 pull+검증이 그 불변식을 계승한다. **이 폴백 제거 금지** — 빼면 낡은 코드가 조용히 돈다(에러 없이).
- **startup-script는 VM 메타데이터에 사본으로 저장**된다. 이제 CI(`update-trade-vm` 잡)가 main 머지마다 자동 갱신한다. 수동(break-glass):
  `gcloud compute instances add-metadata coin-trade-vm --zone us-central1-a --metadata-from-file startup-script=infra/trade-vm-startup.sh`
- **배포 검증(필수)**: CI `healthcheck` 잡이 `/healthz`의 sha를 확인한다(데이터 VM). 매매 VM은 start 후 시리얼 콘솔:
  `gcloud compute instances get-serial-port-output coin-trade-vm --zone us-central1-a | grep -E "boot |trade_once|stock-trade|us-trade"`
- **주식 주간 모의 리밸런싱(KIS)**: `trade-vm-startup.sh`가 부팅시각 분기로 `stock-trade-once`(KR, 15:00 KST 평일)·`us-trade-once`(US, 15:30 ET 평일)를 실행 — 주간 마커가 주 1회 보장. ⚠ **VM `.env`에 KIS 자격증명 필요**(`KIS_APPKEY`/`KIS_APPSECRET`/`KIS_ACCOUNT_NO`/`KIS_MOCK=true`) — Secret Manager `kis-env`로 주입. 자본 제약상 `--top-n`은 자본/주가에 맞게(₩10M이면 top-10 내외).
  → 코인 로그는 `[trade_once] done — decisions=N recorded` 가 보여야 최신 코드. 매매 안 한 날도 `decisions=N`(HOLD 포함) 기록된다.
- **텔레그램 매매 알림**: 매 잡 실행이 결과·오류를 텔레그램으로 발송(`common/notify_telegram`, MTProto 사용자 세션). 자격증명은 Secret Manager **`telegram-env`**(`TELEGRAM_API_ID/API_HASH/SESSION/TARGET` — 발급은 로컬에서 `python -m scripts.telegram_login`)로 `.env`에 주입되며, VM SA에 해당 시크릿 `secretAccessor` 바인딩 필요. 미설정이면 발송만 조용히 스킵(매매 무영향). 잡이 뜨기 전 실패(빌드 등)는 startup `notify_fail`이 앱 이미지 CLI로 폴백 발송.

### 접속 (SSH 터널)
```bash
gcloud compute ssh coin-trader-vm --zone=us-central1-a -- -L 3000:localhost:3000 -L 8000:localhost:8000
# 브라우저 http://localhost:3000 (admin/admin), API http://localhost:8000
```

### 웹 대시보드 (인터넷 공개 — 구글 OAuth 보호)

실시간 시세·잔고·포지션·주문·체결을 한 화면에서 보는 대시보드(`/`)를 인터넷에 공개한다.
주문 가능한 API라 **반드시 구글 OAuth(GOOGLE_CLIENT_ID/SECRET + ALLOWED_EMAILS)를 설정**해 보호한다 — 미설정 시 인증이 비활성이므로 공개 금지.

1. VM `.env` 에 외부 노출 + OAuth 설정:
   ```bash
   API_BIND=0.0.0.0
   GOOGLE_CLIENT_ID=<발급값> / GOOGLE_CLIENT_SECRET=<발급값>
   ALLOWED_EMAILS=<본인 이메일> / SESSION_SECRET=<openssl rand -hex 32>
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
4. 접속: `http://<VM_EXTERNAL_IP>:8000` (구글 로그인). 외부 IP 확인:
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
4. **VM `.env`**: `SITE_ADDRESS=<도메인>` 설정, `API_BIND` 은 비워둠(루프백), 구글 OAuth 키(GOOGLE_CLIENT_ID/SECRET·ALLOWED_EMAILS·SESSION_SECRET) 설정.
5. 재기동: `docker compose --profile data up -d`. Caddy가 인증서를 자동 발급.
6. 접속: **`https://<도메인>`** (Basic Auth 입력). 인증서 발급에 80 포트로의 도달이 필요하다.

### 💰 비용 절감 — 안 쓸 때 VM 중지/삭제 (중요)
```bash
gcloud compute instances stop  coin-trader-vm --zone=us-central1-a   # 중지(디스크만 ~$2/월)
gcloud compute instances start coin-trader-vm --zone=us-central1-a   # 재시작(startup이 재기동)
gcloud compute instances delete coin-trader-vm --zone=us-central1-a  # 완전 삭제(과금 종료)
```
> e2-medium 가동 시 약 $24/월, 중지 시 디스크만 ~$2/월. 학습 후 **중지 또는 삭제** 권장.

### ⏰ 매매 스케줄러 (Cloud Scheduler 6잡 → instances/start)

정의·갱신은 [infra/setup-cicd.sh](infra/setup-cicd.sh) §8의 upsert가 정본(재실행 안전). 모든 잡이 같은 매매 VM을 기동하고 startup이 **UTC 부팅시각으로 분기**한다:

| 잡 | Cron | TZ | 부팅 UTC시 | 분기 |
|---|---|---|---|---|
| `trade-vm-daily` | `0 1 * * *` | UTC | 01 | 코인 (KST 10:00, 매일) |
| `trade-vm-daily-sweep` | `0 2 * * *` | UTC | 02 | 코인 (재시도) |
| `trade-vm-kr-close` | `0 15 * * 1-5` | Asia/Seoul | 06 | KR 주식 (마감 30분 전) |
| `trade-vm-kr-close-sweep` | `10 15 * * 1-5` | Asia/Seoul | 06 | KR 주식 (재시도) |
| `trade-vm-us-close` | `30 15 * * 1-5` | America/New_York | 19(EDT)/20(EST) | US 주식 (마감 30분 전) |
| `trade-vm-us-close-sweep` | `45 15 * * 1-5` | America/New_York | 19/20 | US 주식 (재시도) |

startup 분기표: **06·07→KR / 19·20·21→US / 그 외→코인** (07·21은 부팅지연 여유).

- **스위퍼 근거**: `instances.start`는 존 용량 부족(`ZONE_RESOURCE_POOL_EXHAUSTED`) 등으로 실패할 수 있고 Scheduler는 디스패치 성공만 보므로 재시도가 없다(실사례: 2026-06-30~07-01 3연속 실패로 매매 유실). 스위퍼가 10~60분 뒤 재기동한다. 이중 실행은 멱등 — 코인은 목표 수렴(재실행 주문 0), 주식은 `week_done` skip(수 초 내 poweroff).
- **엣지**: RUNNING 중 start 호출 = startup 재실행 없음(무해). 메인 런 중 스위퍼 발화 = start 실패로 유실될 수 있으나 코인=익일, 주식=익 평일 마커 재시도가 커버. KR 스윕 주문(~15:13)은 연속매매(≤15:20) 또는 동시호가(15:20~15:30 단일가) 체결. NYSE 반일장(13:00 마감)은 그날 미체결 → 마커가 익 평일 재시도.

### 🔔 VM 기동 실패 알림 (Cloud Monitoring)

`setup-cicd.sh` §7이 로그기반 알림을 만든다: `instances.start`의 `severity>=ERROR` 감사로그 매치 → **이메일**(mywinningtime@gmail.com), 1시간 레이트리밋. 메일이 오면 존 용량 부족·쿼터·IAM 실패 중 하나 — 스위퍼가 곧 재시도하므로 보통 조치 불요. 같은 날 반복되면 수동 재시도(분기 시간대 안에서만):
```bash
gcloud scheduler jobs run trade-vm-us-close-sweep --location=us-central1   # US: UTC 19~21시 내
gcloud scheduler jobs run trade-vm-kr-close-sweep --location=us-central1   # KR: UTC 06~07시 내
```

### 🚀 CI/CD (GitHub Actions → Artifact Registry → VM)

main 머지마다 [.github/workflows/deploy.yml](.github/workflows/deploy.yml)이 실행된다:
1. **build**(matrix 병렬): app/batch 이미지 빌드 — buildx **레지스트리 캐시**(`:buildcache`)로 pip 레이어 재사용 → 캐시 히트 시 이미지당 1~2분. 태그 `:latest`+`:<sha>`, `org.opencontainers.image.revision` 라벨.
2. **deploy-data-vm**: `gcloud compute ssh`로 git reset + [infra/deploy-data-vm.sh](infra/deploy-data-vm.sh)(pull→up→sha 검증) 실행.
3. **update-trade-vm**(2와 병렬): 매매 VM 메타데이터 startup-script 갱신.
4. **healthcheck**: `/healthz`의 sha == 배포 커밋 확인(최대 2분 대기).

- 인증 = **Workload Identity Federation**(`github-pool`/`github-provider`, 레포 `SIDED00R/quant-trader` 핀) — GitHub 시크릿 저장 없음. 1회 셋업 = `bash infra/setup-cicd.sh`.
- **롤백**: 원인 커밋 revert 후 머지(권장). 비상시: `gcloud artifacts docker tags add <AR>/quant-trader-app:<이전sha> <AR>/quant-trader-app:latest` 후 VM 재기동.
- ⚠ **OS Login 활성화 금지**(프로젝트/인스턴스 모두) — 데이터 VM `tunnel@` 터널과 CI SSH가 전부 메타데이터 SSH 키 방식이라 OS Login이 켜지면 즉시 끊긴다.
