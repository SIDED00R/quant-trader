# GCP 배포

실제 프로덕션 배포(GCE 2-VM + docker-compose) 절차·런북. 아키텍처 요약은 아래 "현재 배포 상태", 상세 절차는 "실제 배포" 절.

- 프로젝트: `coin-auto-trader-jvfhgq`
- 계정: `mywinningtime@gmail.com`

> ## 현재 배포 상태 (2026-07-02): **라이브 배포됨 (모의) — 코인+주식(KR/US) 2-VM 온디맨드 + CI/CD 자동배포**
> - **틱 수집 VM(상시)**: GCE `coin-trader-vm`(us-central1-a, **e2-small**), `--profile collector`로 **Kafka+ClickHouse+Postgres+WS 레코더(틱)만**(대시보드·매매 제외). 부팅 시 git pull + AR 이미지 pull. ≈$12/월.
> - **CI/CD**: main 머지 → GitHub Actions([.github/workflows/deploy.yml](.github/workflows/deploy.yml))가 이미지 병렬 빌드(레지스트리 캐시)→Artifact Registry 푸시→**수집 VM 배포(`--profile collector`)**+매매 VM 메타데이터 갱신. 배포 검증=이미지 revision(sha) 라벨 대조([infra/deploy-collector-vm.sh](infra/deploy-collector-vm.sh)). 1회 셋업 = [infra/setup-cicd.sh](infra/setup-cicd.sh).
> - **매매 VM(온디맨드)**: GCE `coin-trade-vm`, 평소 **정지(TERMINATED)**. Cloud Scheduler **8잡(메인 4 + 스위퍼 4)** 이 같은 VM을 `instances/start` 기동(startup이 UTC 부팅시각으로 분기):
>   - **코인**: `trade-vm-daily` 매일 01:00 UTC(KST 10:00) + 스위퍼 02:00 UTC → `trade_once` → poweroff.
>   - **KR 주식**: `trade-vm-kr-close` 평일 15:00 KST(마감 30분 전) + 스위퍼 15:10 KST → `stock_trade_once`.
>   - **US 주식**: `trade-vm-us-close` 평일 15:00 ET(마감 1시간 전 — 부팅 지연 ~30분 흡수→주문 마감 30분 전 도달, DST 자동) + 스위퍼 15:15 ET → `us_trade_once`.
>   - **스위퍼 = 기동실패 재시도**: 존 용량 부족(`ZONE_RESOURCE_POOL_EXHAUSTED`, 실사례 2026-06-30~07-01 3연속) 시 수 분 뒤 재기동. 이중 실행은 멱등(코인=목표 수렴, 주식=주간 마커). 기동 실패는 이메일 알림(아래 'VM 기동 실패 알림' 절).
>   - **주 1회 멱등(휴장·실패 재시도)**: 주식은 평일마다 부팅·시도하지만 `weekly_rebalance` 마커가 **주 1회만 실제 매매**를 보장. 휴장일(US=NYSE 게이트, KR=체결기반)·일시적 실패면 그 주 마커를 남기지 않아 **다음 평일 자동 재시도**. 체결 자체는 `kis_chase`(미체결 취소·재주문 추격)가 보장.
>   - 매매 VM **로컬 postgres/clickhouse**(자기완결, 크로스VM 터널 없음), 동기 매매. 가동시간만 과금(~$1/월).
> - **대시보드(온디맨드)**: 계좌/주문 Postgres가 매매 VM에 있어 대시보드도 그 VM 상주. 별도 **gcp-cost-controller**(텔레그램)에서 `/start quant-vm`(대시보드 모드) → 조회 → `/stop quant-vm`(미종료 시 2h 자동 종료). 조회 2경로: **① 공개 HTTPS** `https://jh-quantlab.duckdns.org`(Secret Manager `web-env`=SITE_ADDRESS·구글 OAuth 주입 시 startup이 Caddy 동반 기동, 인증서 자동발급, OAuth 보호) · **② SSH 터널** 폴백(`gcloud compute ssh coin-trade-vm -- -L 8000:localhost:8000`). **상시** 공개는 아님(대시보드 모드가 떠 있는 동안만 노출).
> - **라이브 매매 경로**: `trade_once`(동기 배치). 스트리밍 `commander`/`engine`/`portfolio`는 코드로만 존재(로컬 dev). **Kafka는 데이터 팬아웃만**(매매 미사용).
> - **데이터**: ClickHouse candles_1d(BTC/ETH 2019-11~) + 전 KRW 마켓 틱 상시 수집. **모의 거래**(실거래 API 없음) — 코인 가상잔고 ₩10M, 주식 KIS 모의계좌(KR ₩10M·US $100k). 모델 출처 = `docs/model.md`.
> - **상시 비용 ~$66 → ~$13/월** (단일 상시 VM 풀스택 → e2-small 틱 수집 VM + 온디맨드 매매/대시보드로 분리).

---

## 실제 배포 (GCE 2-VM + docker-compose)

> ⚠️ **일부 절차는 분리(collector/온디맨드) 이전 기준이다.** 최신 토폴로지·비용은 위 "현재 배포 상태" 요약(§상단)을 따른다:
> 상시 VM은 이제 **틱 수집 전용 `--profile collector`(e2-small)**, 대시보드·Postgres·매매는 **온디맨드 매매 VM**(로컬 DB, gcp-cost-controller `/start quant-vm`으로 대시보드 조회). 배포는 `deploy-collector-vm.sh`, 공개 `/healthz` 헬스체크는 제거(이미지 sha 라벨 대조로 대체). 아래 상세 단계 중 `--profile data`·`/healthz`·SSH 터널 DB·공개 대시보드 서술은 갱신 대상.

비용 최소화를 위해 매니지드 서비스 대신 **단일 GCE VM에 docker-compose 풀스택**으로 배포했다.

- 프로젝트: `coin-auto-trader-jvfhgq` — 수집 VM `us-central1-a`(e2-small) / 매매 VM **서울 `asia-northeast3-a`**(e2-standard-2, us-central1 용량 고갈 회피 + 한국 거래소/브로커 근접, #266). AR·Cloud Scheduler는 `us-central1` 유지(교차리전 이미지 pull은 부팅 디스크 레이어 캐시로 미미)
- VM: `coin-trader-vm`, `e2-small`(2vCPU 공유/2GB), `pd-standard` 30GB, Ubuntu 22.04
- 부팅 스크립트: [infra/collector-vm-startup.sh](infra/collector-vm-startup.sh)(수집 VM — Docker 설치 → 레포 클론 → AR pull(`--build` 폴백) → `docker compose --profile collector up -d`, 24/7 틱 수집 + 4GB 스왑) · [infra/trade-vm-startup.sh](infra/trade-vm-startup.sh)(매매 VM — 부팅시각 분기 매매/유지보수 → poweroff, 대시보드 모드 지원).
- 컨테이너 이미지: Artifact Registry `us-central1/docker`의 `quant-trader-app`/`quant-trader-batch` — CI가 빌드·푸시(`:latest`+`:<sha>`+`:buildcache`), VM은 pull.
- 예산 알림: 결제계정에 30,000 (50/90/100%) 설정됨
- 포트는 VM 루프백에만 바인딩 → 외부 비노출, 접속은 SSH 터널

### ⚠️ 배포 함정 — 코드가 실제로 적용됐는지 반드시 확인

- **매매 VM은 코드를 이미지에 굽는다**(소스 볼륨 마운트 없음). 부팅 시 `git reset` 후 AR `:latest`를 pull하고 **이미지의 `org.opencontainers.image.revision` 라벨 vs `git rev-parse HEAD`를 검증** — 불일치·pull 실패면 `--build`로 최신 소스 재빌드(`build_flag()` 헬퍼). 예전엔 무조건 `--build`였고(#94 결정 기록 미동작 → #99 복구), 지금은 pull+검증이 그 불변식을 계승한다. **이 폴백 제거 금지** — 빼면 낡은 코드가 조용히 돈다(에러 없이).
- **startup-script는 VM 메타데이터에 사본으로 저장**된다. 이제 CI(`update-trade-vm` 잡)가 main 머지마다 자동 갱신한다. 수동(break-glass):
  `gcloud compute instances add-metadata coin-trade-vm --zone asia-northeast3-a --metadata-from-file startup-script=infra/trade-vm-startup.sh`
- **배포 검증(필수)**: 수집 VM은 CI `deploy-collector-vm` 잡이 배포 후 **이미지 sha 라벨 == 배포 커밋** 검증까지 수행한다(`deploy-collector-vm.sh`). 매매 VM은 start 후 시리얼 콘솔:
  `gcloud compute instances get-serial-port-output coin-trade-vm --zone asia-northeast3-a | grep -E "boot |trade_once|stock-trade|us-trade"`
- **주식 주간 모의 리밸런싱(KIS)**: `trade-vm-startup.sh`가 부팅시각 분기로 `stock-trade-once`(KR, 15:00 KST 평일)·`us-trade-once`(US, 15:00 ET 평일)를 실행 — 주간 마커가 주 1회 보장. ⚠ **VM `.env`에 KIS 자격증명 필요**(`KIS_APPKEY`/`KIS_APPSECRET`/`KIS_ACCOUNT_NO`/`KIS_MOCK=true`) — Secret Manager `kis-env`로 주입. 자본 제약상 `--top-n`은 자본/주가에 맞게(₩10M이면 top-10 내외).
  → 코인 로그는 `[trade_once] done — decisions=N recorded` 가 보여야 최신 코드. 매매 안 한 날도 `decisions=N`(HOLD 포함) 기록된다.
- **텔레그램 매매 알림**: 매 잡 실행이 결과·오류를 텔레그램으로 발송(`common/notify_telegram`, MTProto 사용자 세션). 자격증명은 Secret Manager **`telegram-env`**(`TELEGRAM_API_ID/API_HASH/SESSION/TARGET` — 발급은 로컬에서 `python -m scripts.telegram_login`)로 `.env`에 주입되며, VM SA에 해당 시크릿 `secretAccessor` 바인딩 필요. 미설정이면 발송만 조용히 스킵(매매 무영향). 잡이 뜨기 전 실패(빌드 등)는 startup `notify_fail`이 앱 이미지 CLI로 폴백 발송.
- **토스 시크릿(`toss-env`)**: 매매 전 일봉 증분 갱신(`refresh_stock_daily`)이 토스 API를 호출하므로 kis-env·telegram-env와 동일 패턴으로 생성한다.
  ```bash
  printf 'TOSS_CLIENT_ID=...\nTOSS_CLIENT_SECRET=...\n' | gcloud secrets create toss-env --data-file=-
  gcloud secrets add-iam-policy-binding toss-env --member="serviceAccount:<매매 VM SA>" --role="roles/secretmanager.secretAccessor"
  ```
- **DART 시크릿(`dart-env`)**: 데이터 유지보수(`batch.data.maintenance_once`)가 DART OpenAPI(`kr_fundamentals`)를 호출하므로 toss-env와 동일 패턴으로 생성한다.
  ```bash
  printf 'DART_API_KEY=...\n' | gcloud secrets create dart-env --data-file=-
  gcloud secrets add-iam-policy-binding dart-env --member="serviceAccount:<매매 VM SA>" --role="roles/secretmanager.secretAccessor"
  ```
- **KRX·FRED 시크릿(`krx-env`·`fred-env`)**: 연구 데이터 지속 수집 스텝(KRX 수급·공매도·외국인보유·지수 PIT / FRED 매크로)이 사용한다 — toss-env와 동일 패턴:
  ```bash
  printf 'KRX_ID=...\nKRX_PW=...\n' | gcloud secrets create krx-env --data-file=-
  printf 'FRED_API_KEY=...\n' | gcloud secrets create fred-env --data-file=-
  for s in krx-env fred-env; do gcloud secrets add-iam-policy-binding $s --member="serviceAccount:<매매 VM SA>" --role="roles/secretmanager.secretAccessor"; done
  ```
  **KRX 초회 선시딩(1회)**: 프로드 테이블(stock_investor_flow·stock_foreign_holding·stock_short KR)이 비어 있으면 월간 증분(`krx.py`)이 raise한다(암묵 수시간 per-symbol 전량 백필 차단). by-date 고속 수집기로 1회 시딩 후 증분에 맡긴다:
  `docker compose --profile trade run --rm maintenance-once python -m batch.data.krx_bulk --start 2018-01-01`  *(분 단위·by-date 8콜/일. 주의: bulk는 수급 3분류(외국인·기관합계·연기금)만 — 이후 월간 증분(krx.py)은 11분류 원본 수집)*
- **정기 데이터 유지보수(`maintenance-once`, #204)**: 매매 전 증분 갱신(14일)이 다루지 못하는 수정주가 기준 재조정·분기 공시 반영을 매월 첫 토요일 04:00 UTC에 1회 실행한다(활성 유니버스 일봉 선별 재백필(재조정/데이터갭 종목만) + EDGAR·13F·SIC·DART 수집기 재실행 + **연구 데이터 지속 수집**: KRX 수급·공매도·외국인보유(증분)·KR/US 지수 PIT 멤버십·FRED 매크로·KR 상폐 메타 — 모델 미사용이어도 재사용 자산으로 축적, 수집기별 0행 가드로 조용한 실패 차단). 단계별 격리 + 텔레그램 통보.
- **원시 틱 TTL 180일 — 기존(라이브) 테이블 1회 ALTER 런북**: 스키마의 TTL은 신규 설치 전용(`db/clickhouse_schema.sql` 주석 — init_db가 매 부팅 재실행하므로 ALTER를 스키마에 넣으면 매번 재물질화). 수집 VM에 1회 적용:
  ```bash
  gcloud compute ssh coin-trader-vm --zone=us-central1-a --command \
   "cd /opt/coin-auto-trader && P=\$(sudo grep -E '^CLICKHOUSE_PASSWORD=' .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//; s/[[:space:]]*\$//'); \
    for T in ticks stock_ticks; do sudo docker exec clickhouse clickhouse-client --password \"\${P:-ch_pw}\" \
      -q \"ALTER TABLE coin_analytics.\$T MODIFY TTL toDateTime(trade_ts) + INTERVAL 180 DAY\"; done"
  # 확인: SELECT name, engine_full FROM system.tables WHERE database='coin_analytics' AND name IN ('ticks','stock_ticks')
  # 테이블 나이 < 180일인 지금 실행하면 만료 0행이라 재물질화 부하도 사실상 없음.
  # (매매 VM 로컬 ticks 테이블은 비어 있어 무관 — 다음 재생성 시 스키마 TTL이 자연 적용)
  ```
- **수집 VM 헬스체크(`infra/collector-healthcheck.sh`) 활성화**: cron(30분)이 디스크(≥80%)·필수 컨테이너 다운·틱 유입 정지(코인 24/7, 주식 KRX 장중)를 검사해 텔레그램 통보(검사키별 6h 쿨다운, 회복 시 재무장). 1회 셋업:
  ```bash
  # ① 수집 VM SA에 telegram-env 접근 허용(주입은 startup이 수행)
  gcloud secrets add-iam-policy-binding telegram-env --member="serviceAccount:<수집 VM SA>" --role="roles/secretmanager.secretAccessor"
  # ② 이미 켜져 있는 VM에 즉시 적용(다음 부팅부터는 startup이 자동): SSH로 startup 스크립트 재실행 또는
  #    git pull 후 cron 파일만 수동 설치 + telegram-env를 .env에 수동 병합
  # 드라이런: sudo DISK_MAX=1 bash infra/collector-healthcheck.sh → 🟠 수신 확인, 재실행 시 쿨다운 억제 확인,
  #           sudo rm /var/tmp/collector-healthcheck.state 로 리셋
  ```
- **텔레그램 `/chart` 봇(수집 VM 상시 `telegram-bot` 서비스, 제어봇과 별도 봇)**: `/chart <종목명|티커>`(한글 `/차트` 별칭) → 봉차트(KR 주봉+일목·US 일봉) 응답. gcp-cost-controller(제어봇 jhgcpcaller, 서버리스)와 **분리** — 서버리스 egress IP는 가변이라 아래 Toss IP 허용목록을 못 맞춘다(그래서 상시 수집 VM에 얹음). 1회 셋업:
  ```bash
  # ⚠️ 핵심: Toss OpenAPI는 IP 허용목록 방식(미등록 IP는 토큰 발급부터 403 access_denied). 수집 VM은
  #    예약 고정 IP가 있어야 하고, 그 IP를 Toss 개발자 콘솔 허용목록에 등록해야 봇/매매 데이터 fetch가 동작.
  # ① 고정 IP 예약 + 수집 VM에 부여(부팅마다 바뀌는 임시 IP 금지 — 등록이 stale돼 403). 이미 예약돼 있으면 create 생략:
  gcloud compute addresses create coin-trader-vm-ip --region=us-central1   # 예약값: 136.113.2.241 (기존 coin-trader-ip와 혼동 주의)
  AC=$(gcloud compute instances describe coin-trader-vm --zone=us-central1-a --format="value(networkInterfaces[0].accessConfigs[0].name)")  # 대개 external-nat 또는 'External NAT'
  gcloud compute instances delete-access-config coin-trader-vm --zone=us-central1-a --access-config-name="$AC"
  gcloud compute instances add-access-config    coin-trader-vm --zone=us-central1-a --access-config-name="external-nat" --address=coin-trader-vm-ip
  #    → 그 고정 IP(136.113.2.241)를 Toss 콘솔 허용목록에 등록(운영자 수동, 기존 34.28.69.174 대체 가능).
  # ② 봇 생성(BotFather) → 토큰. (선택) /setcommands: chart - 종목 봉차트 조회 (등록은 ASCII만 → /chart, /차트는 타이핑만)
  # ③ telegram-env 시크릿에 2줄 append(기존 MTProto 키 유지) — startup의 ^TELEGRAM_ prefix 병합으로 자동 반영:
  #    TELEGRAM_BOT_TOKEN=123456:ABC...  /  TELEGRAM_ALLOWED_CHAT_IDS=<chat_id>  (새 버전 add)
  # ④ 봇용 toss-env(일봉 fetch)·telegram-env(봇 토큰)를 수집 VM SA에 바인딩(주입은 startup의 각 블록이 수행):
  for s in toss-env telegram-env; do gcloud secrets add-iam-policy-binding $s --member="serviceAccount:<수집 VM SA>" --role="roles/secretmanager.secretAccessor"; done
  # ⑤ 수집 VM 재부팅(startup이 최신 main pull + telegram-bot 기동) 또는 `docker compose --profile collector up -d telegram-bot`
  # 주의: Bot API getUpdates는 단일 소비자만 허용 — 로컬 테스트는 반드시 별도 테스트 토큰 사용(운영 토큰 동시 폴링 시 409).
  # 주의: Toss 토큰은 클라이언트당 1개 — 봇/매매 잡이 같은 client_id면 상호 무효화 가능(봇은 401 자가재발급, 잡은 다음 부팅 회복).
  ```
- **관심종목 검색 종목명 첫 시딩(선택)**: CH `stock_names`는 db-init이 테이블만 만들고 실제 이름은 월간 maintenance(`_stock_names_step`)가 채운다. 첫 월간 실행 전 대시보드 이름검색을 쓰려면 매매 VM에서 1회 수동 시딩(없어도 티커 검색·관심종목 등록·`watchlist-charts`는 정상 동작):
  ```bash
  docker compose --profile trade run --rm maintenance-once python -c "from common.clickhouse_client import create_client; from common.stock_names import refresh_clickhouse; print(refresh_clickhouse(create_client()))"
  ```
- **매매 VM 절대 워치독**: startup 시작 직후 `shutdown -P +90`(유지보수 분기 +360, 대시보드 +120으로 재예약) — 어떤 단계가 행이어도 과금 상한. 정상 경로는 말미 `poweroff`가 선행돼 무해.
- **초기 데이터 시딩 런북**(프로덕션 `stock_candles_1d` 빈 테이블 복구·최초 구축):
  1. 연구 ClickHouse → 프로드 ClickHouse로 4테이블(`stock_candles_1d`·`fundamentals_quarterly`·`institutional_13f`·`stock_meta`) 복사. SSH 터널로 두 CH에 접속해 `clickhouse_connect`로 조회→삽입.
  2. 활성 유니버스 전체 재백필(수정주가 기준 통일):
     `python -m batch.backtest.backfill_stock_daily --symbols-file <kospi200+kosdaq150+sp500+nasdaq100 결합 파일> --days 2600`
     신규 종목 편입도 이 CLI로 수행한다(`refresh_stock_daily`는 이미 활성인 종목의 증분만 갱신, 신규 편입은 다루지 않음).

### 접속 (SSH 터널)
```bash
gcloud compute ssh coin-trader-vm --zone=us-central1-a -- -L 3000:localhost:3000 -L 8000:localhost:8000
# 브라우저 http://localhost:3000 (admin/admin), API http://localhost:8000
```

### 웹 대시보드 (인터넷 공개 — 구글 OAuth 보호)

실시간 시세·잔고·포지션·주문·체결을 한 화면에서 보는 대시보드(`/`)를 인터넷에 공개한다.
주문 가능한 API라 **반드시 구글 OAuth(GOOGLE_CLIENT_ID/SECRET + ALLOWED_EMAILS)를 설정**해 보호한다 — 미설정 시 인증이 비활성이므로 공개 금지.

> ⚠ 이 절은 (구) **수집 VM 상시 공개** 절차의 참고 기록이다 — 현행 대시보드는 **온디맨드 매매 VM dashboard 모드**
> (`https://jh-quantlab.duckdns.org`, 다음 절 ⚠ 참조)로 뜬다. 수집 VM 현행 프로파일(`collector`)엔 api가 없어
> 아래 절차는 레거시 `data` 프로파일 기준이다(상시 공개를 다시 켤 때만 참고).

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

1. **임시(ephemeral) IP + DuckDNS 동적 갱신**(온디맨드 매매 VM 권장) — 정적 IP를 예약하면 VM 정지 중에도 idle 과금(~$7/월)된다. 대신 임시 IP를 쓰고 부팅 시 현재 공인 IP를 DuckDNS에 자동 갱신한다:
   ```bash
   # Secret Manager duckdns-env 생성(토큰=duckdns.org 계정) + VM SA 바인딩
   printf 'DUCKDNS_TOKEN=<토큰>\nDUCKDNS_DOMAIN=jh-quantlab\n' | gcloud secrets create duckdns-env --data-file=- --project=$PROJECT
   gcloud secrets add-iam-policy-binding duckdns-env --project=$PROJECT \
     --member="serviceAccount:$(gcloud compute instances describe coin-trade-vm --zone=asia-northeast3-a --project=$PROJECT --format='value(serviceAccounts[0].email)')" \
     --role=roles/secretmanager.secretAccessor
   # 기존 정적 IP를 쓰고 있었다면 해제(ephemeral 전환 후 주소 삭제)
   gcloud compute instances delete-access-config coin-trade-vm --zone=asia-northeast3-a --access-config-name="external-nat" --project=$PROJECT
   gcloud compute instances add-access-config coin-trade-vm --zone=asia-northeast3-a --access-config-name="external-nat" --project=$PROJECT
   gcloud compute addresses delete coin-trader-ip --region=us-central1 --project=$PROJECT
   ```
   `infra/trade-vm-startup.sh` dashboard 분기가 부팅마다 metadata의 external-ip를 `duckdns.org/update`로 갱신한다(DUCKDNS_TOKEN 미설정 시 no-op — 정적 IP 유지 구성과 호환).
2. **방화벽 80/443 허용**(8000 직접 노출은 더 이상 불필요):
   ```bash
   gcloud compute firewall-rules create allow-web-https --project=$PROJECT --network=default \
     --direction=INGRESS --action=ALLOW --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=coin-web
   ```
4. **VM `.env`**: `SITE_ADDRESS=<도메인>` 설정, `API_BIND` 은 비워둠(루프백), 구글 OAuth 키(GOOGLE_CLIENT_ID/SECRET·ALLOWED_EMAILS·SESSION_SECRET) 설정.
5. 재기동: `docker compose --profile data up -d`. Caddy가 인증서를 자동 발급.
6. 접속: **`https://<도메인>`** (구글 OAuth 로그인 — ALLOWED_EMAILS 제한). 인증서 발급에 80 포트로의 도달이 필요하다.

> ⚠ 이 절은 **상시 공개** 대시보드용 절차다. 현행은 **온디맨드 공개** — 매매 VM 대시보드 모드가 떠 있는 동안만
> `https://jh-quantlab.duckdns.org`로 노출된다(§상단 요약). 그 배선은 이미 되어 있다: Secret Manager `web-env`
> (SITE_ADDRESS·GOOGLE_CLIENT_ID/SECRET·ALLOWED_EMAILS·SESSION_SECRET) + `coin-trade-vm`의 `coin-web` 태그 +
> `infra/trade-vm-startup.sh` dashboard 분기(SITE_ADDRESS·GOOGLE_CLIENT_ID 있으면 Caddy 동반 기동). 아래 정적 IP/DuckDNS/정적
> Caddyfile 설명은 상시 공개를 다시 켤 때만 참고.

### 💰 비용 절감 — 안 쓸 때 VM 중지/삭제 (중요)
```bash
gcloud compute instances stop  coin-trader-vm --zone=us-central1-a   # 중지(디스크만 ~$2/월)
gcloud compute instances start coin-trader-vm --zone=us-central1-a   # 재시작(startup이 재기동)
gcloud compute instances delete coin-trader-vm --zone=us-central1-a  # 완전 삭제(과금 종료)
```
> e2-small 가동 시 약 $12/월, 중지 시 디스크만 ~$2/월. 학습 후 **중지 또는 삭제** 권장.

### ⏰ 매매 스케줄러 (Cloud Scheduler 8잡 → instances/start)

정의·갱신은 [infra/setup-cicd.sh](infra/setup-cicd.sh) §8의 upsert가 정본(재실행 안전). 모든 잡이 같은 매매 VM을 기동하고 startup이 **UTC 부팅시각으로 분기**한다:

| 잡 | Cron | TZ | 부팅 UTC시 | 분기 |
|---|---|---|---|---|
| `trade-vm-daily` | `0 1 * * *` | UTC | 01 | 코인 (KST 10:00, 매일) |
| `trade-vm-daily-sweep` | `0 2 * * *` | UTC | 02 | 코인 (재시도) |
| `trade-vm-kr-close` | `0 15 * * 1-5` | Asia/Seoul | 06 | KR 주식 (마감 30분 전) |
| `trade-vm-kr-close-sweep` | `10 15 * * 1-5` | Asia/Seoul | 06 | KR 주식 (재시도) |
| `trade-vm-us-close` | `0 15 * * 1-5` | America/New_York | 19(EDT)/20(EST) | US 주식 (마감 1시간 전) |
| `trade-vm-us-close-sweep` | `15 15 * * 1-5` | America/New_York | 19/20 | US 주식 (재시도) |
| `trade-vm-maintenance` | `0 4 * * 6` | UTC | 04 | 데이터 유지보수 (매주 토 발화 — 첫 주만 실행해 월간화) |
| `trade-vm-maintenance-sweep` | `0 5 * * 6` | UTC | 05 | 데이터 유지보수 (재시도) |

startup 분기표: **04·05→유지보수(토·1~7일 가드) / 06·07→KR / 19·20·21→US / 그 외→코인** (07·21은 부팅지연 여유).

- **스위퍼 근거**: `instances.start`는 존 용량 부족(`ZONE_RESOURCE_POOL_EXHAUSTED`) 등으로 실패할 수 있고 Scheduler는 디스패치 성공만 보므로 재시도가 없다(실사례: 2026-06-30~07-01 3연속 실패로 매매 유실). 스위퍼가 10~60분 뒤 재기동한다. 이중 실행은 멱등 — 코인은 목표 수렴(재실행 주문 0), 주식은 `week_done` skip(수 초 내 poweroff).
- **엣지**: RUNNING 중 start 호출 = startup 재실행 없음(무해). 메인 런 중 스위퍼 발화 = start 실패로 유실될 수 있으나 코인=익일, 주식=익 평일 마커 재시도가 커버. KR 스윕 주문(~15:13)은 연속매매(≤15:20) 또는 동시호가(15:20~15:30 단일가) 체결. NYSE 반일장(13:00 마감)은 그날 미체결 → 마커가 익 평일 재시도.

### 📈 자산 차트 발행 (assets 브랜치 — README 자동 갱신)

각 매매 잡이 종료 시 시장별 평가자산을 `equity_snapshots`에 upsert하고(코인=잡 내부 훅, KR/US=`main()`에서 KIS 잔고 재조회 — 주간 스킵 날도 기록), startup 말미가 `equity-chart` 컨테이너(app 이미지, `scripts/render_equity_chart.py`)로 SVG 라이트/다크 2벌을 렌더해 **orphan `assets` 브랜치에 단일 커밋 force-push**한다. README `<picture>`가 `raw.githubusercontent.com/SIDED00R/quant-trader/assets/equity-{light,dark}.svg`를 참조.

- **설계 근거**: DB가 매매 VM 로컬이라 GitHub Actions에선 접근 불가 → 데이터·git 키·시크릿이 이미 있는 매매 VM에서 발행. force-push 단일 커밋 = 브랜치 크기 SVG 2개 고정(히스토리 무증가). `deploy.yml`은 `push: branches: [main]` 전용이라 **CI 미발화**. raw 이미지는 camo가 ~5분 캐시(일 1~3회 갱신에 무해).
- **쓰기 배포키 1회 셋업** (미준비 동안 push 스텝만 조용히 스킵 — 코드 배포와 독립):
  ```bash
  ssh-keygen -t ed25519 -f gh-push-key -N "" -C trade-vm-assets-push
  gh repo deploy-key add gh-push-key.pub -R SIDED00R/quant-trader --title "trade-vm assets push" --allow-write
  gcloud secrets create github-push-key --data-file=gh-push-key --project=coin-auto-trader-jvfhgq
  gcloud secrets add-iam-policy-binding github-push-key --project=coin-auto-trader-jvfhgq \
    --member="serviceAccount:689150179824-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
  rm gh-push-key gh-push-key.pub
  ```
  기존 읽기 키(`github-deploy-key`, clone/fetch용)와 **분리** — 쓰기 키는 push 스텝에서만 사용(유출 반경 최소화).
- **환율**: '전체(KRW 환산)' 시리즈용 FRED usdkrw는 코인 데일리 잡이 `batch.data.fred`로 일 1회 갱신(`FRED_API_KEY`=기존 `fred-env` 시크릿, 실패 비치명 — 직전 환율 캐리).
- **텔레그램 사진**: 같은 곡선을 코인 데일리 잡이 PNG로 렌더해 텔레그램 사진 1장/일 발송(`common/equity_chart_telegram`, 스위퍼 재실행은 trade_once의 '이미 실행됨' 가드가 차단) — VM을 켜지 않고도 자산 흐름 확인.
- **확인**: 잡 후 `assets` 브랜치 커밋 1개 + README 이미지 갱신. 렌더/발행 로그는 VM `/var/log/equity-chart.log`(`EQUITY_CHART_PUBLISHED` / `EQUITY_CHART_PUSH_FAILED(비치명)`).

### 🔔 VM 기동 실패 알림 (Cloud Monitoring)

`setup-cicd.sh` §7이 로그기반 알림을 만든다: `instances.start`의 `severity>=ERROR` 감사로그 매치 → **이메일**(mywinningtime@gmail.com), 1시간 레이트리밋. 메일이 오면 존 용량 부족·쿼터·IAM 실패 중 하나 — 스위퍼가 곧 재시도하므로 보통 조치 불요. 같은 날 반복되면 수동 재시도(분기 시간대 안에서만):
```bash
gcloud scheduler jobs run trade-vm-us-close-sweep --location=us-central1   # US: UTC 19~21시 내
gcloud scheduler jobs run trade-vm-kr-close-sweep --location=us-central1   # KR: UTC 06~07시 내
```

### 🚀 CI/CD (GitHub Actions → Artifact Registry → VM)

main 머지마다 [.github/workflows/deploy.yml](.github/workflows/deploy.yml)이 실행된다:
1. **build**(matrix 병렬): app/batch 이미지 빌드 — buildx **레지스트리 캐시**(`:buildcache`)로 pip 레이어 재사용 → 캐시 히트 시 이미지당 1~2분. 태그 `:latest`+`:<sha>`, `org.opencontainers.image.revision` 라벨.
2. **deploy-collector-vm**: `gcloud compute ssh`로 git reset + [infra/deploy-collector-vm.sh](infra/deploy-collector-vm.sh)(pull→up→sha 라벨 검증) 실행.
3. **update-trade-vm**(2와 병렬): 매매 VM 메타데이터 startup-script 갱신.

- **test 게이트**: 모든 배포는 pytest(`batch/backtest/tests/`)+전 모듈 import 스윕+프로덕션 경계(batch.* 금지) 검사를 선행한다(`build.needs: test`) — 빨간 코드는 빌드·배포가 시작되지 않는다.
- **CI 실패 알림**: 어느 잡이든 실패하면 `notify-failure` 잡이 WIF로 `telegram-env`를 읽어 텔레그램 통보한다. 1회 셋업:
  `gcloud secrets add-iam-policy-binding telegram-env --member="serviceAccount:github-deployer@coin-auto-trader-jvfhgq.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"`
- 인증 = **Workload Identity Federation**(`github-pool`/`github-provider`, 레포 `SIDED00R/quant-trader` 핀) — GitHub 시크릿 저장 없음. 1회 셋업 = `bash infra/setup-cicd.sh`.
- **롤백**: 원인 커밋 revert 후 머지(권장). 비상시: `gcloud artifacts docker tags add <AR>/quant-trader-app:<이전sha> <AR>/quant-trader-app:latest` 후 VM 재기동.
- ⚠ **OS Login 활성화 금지**(프로젝트/인스턴스 모두) — CI SSH가 메타데이터 SSH 키 방식이라 OS Login이 켜지면 즉시 끊긴다.
