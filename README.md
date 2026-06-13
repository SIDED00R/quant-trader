# coin-auto-trader

실시간 코인 모의거래 시스템 — Kafka 기반 실시간 이벤트 파이프라인 학습 프로젝트.

업비트 실시간 시세를 받아 가상 자금으로 매수/매도하고, 체결·포트폴리오·손익을 실시간으로 보여준다.

## 아키텍처 개요

```
업비트 WS → [market.ticks] → ┬→ ClickHouse(틱/캔들)
                             └→ 체결 엔진 → [executions] → 포트폴리오 → PostgreSQL
사용자 → FastAPI → [orders] ──┘
```

- **메시지**: Apache Kafka (KRaft)
- **OLTP**: PostgreSQL (잔고/주문/포지션)
- **OLAP**: ClickHouse (틱/캔들/분석)
- **API**: FastAPI
- **대시보드**: Grafana

전체 설계는 [DESIGN.md](DESIGN.md) 참고.

## 로컬 실행 (인프라)

```bash
# 1) 환경변수 준비
cp .env.example .env

# 2) 인프라 기동 (Kafka + PostgreSQL + ClickHouse + 토픽 자동 생성)
docker compose up -d

# 3) 검증
docker compose ps                                   # 컨테이너 상태 확인
docker compose logs kafka-init                       # 생성된 토픽 목록 확인
docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

# 4) produce/consume 왕복 테스트
docker exec -i kafka /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 --topic market.ticks   # 입력 후 메시지 타이핑
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic market.ticks --from-beginning --max-messages 1

# 종료
docker compose down            # 컨테이너 제거 (볼륨 유지)
docker compose down -v         # 볼륨까지 삭제
```

접속 정보: Kafka `localhost:9092` · PostgreSQL `localhost:5432` · ClickHouse HTTP `localhost:8123`

## 진행 상태

- [x] 0. 로컬 인프라 (docker-compose)
- [x] 1. Market 수집기 (업비트 WS → market.ticks)
- [x] 2. 틱 Sink → ClickHouse
- [x] 3. 주문 API + Postgres 스키마
- [ ] 4. 체결 엔진 (시장가)
- [ ] 5. 포트폴리오 서비스
- [ ] 6. 캔들 집계기 → ClickHouse
- [ ] 7. 지정가 주문
- [ ] 8. Grafana 대시보드
- [ ] 9. GCP 배포
