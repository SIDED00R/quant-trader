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

## 진행 상태

- [ ] 0. 로컬 인프라 (docker-compose)
- [ ] 1. Market 수집기 (업비트 WS → market.ticks)
- [ ] 2. 틱 Sink → ClickHouse
- [ ] 3. 주문 API + Postgres 스키마
- [ ] 4. 체결 엔진 (시장가)
- [ ] 5. 포트폴리오 서비스
- [ ] 6. 캔들 집계기 → ClickHouse
- [ ] 7. 지정가 주문
- [ ] 8. Grafana 대시보드
- [ ] 9. GCP 배포
