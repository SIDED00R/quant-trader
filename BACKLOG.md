# 자동매매 백로그 (전략 로드맵)

자동매매 봇의 단계별 개발 계획. 모든 전략은 기존 파이프라인을 재사용한다:
`strategy/` → `orders`(+outbox) → `relay` → 체결엔진 → `executions` → 포트폴리오.

각 항목은 **별도 이슈 → 브랜치 → PR** 사이클로 진행한다.

---

## ✅ 1단계 — 기본 전략 (완료: #40/#41)
- [x] 이동평균 교차(SMA): 단기/장기 SMA, 골든크로스 매수 / 데드크로스 매도
- [x] 계정별 자동매매 토글(`accounts.auto_trade`) + 대시보드 ON/OFF
- [x] 안전장치: 주문당 고정 KRW, 종목당 1포지션, 종목별 쿨다운
- [x] 공유 주문 헬퍼(`common/order_writer.py`)로 API·봇 주문 경로 통일

## ✅ 1.5단계 — 규율 기반 강화 (완료: #42)
- [x] 진입/청산 분리, 이격 밴드 + 확인봉 상태머신(휩쏘 방지)
- [x] 손절(stop-loss) / 익절(take-profit) — 평단(avg_buy_price) 기준 매 틱
- [x] 트레일링(고점 무장 후 되돌림) 익절 보호
- [x] (계정,종목)별 재진입 쿨다운·최소보유시간, 이중매도 방지 settle 가드

## ✅ 2단계 — 추가 전략 (완료: #54)
- [x] **RSI**: 과매도(≤30) 매수 / 과매수(≥70) 매도 (mean-reversion) — `strategy/rsi.py`
- [x] **돌파/모멘텀**: 도닉언 채널 돌파 매수 / 이탈 매도 — `strategy/breakout.py`
- [x] **MACD**: 시그널선 교차 — `strategy/macd.py`
- [x] **볼린저 밴드**: 밴드 이탈/회귀 — `strategy/bollinger.py`
- [ ] 계정별 전략 선택 + 파라미터 설정 UI (웹 UI 미구현)

## 3단계 — 리스크/주문 관리 (추후)
- [x] 손절(stop-loss) / 익절(take-profit) — #42 (1.5단계)
- [ ] 포지션 사이징(잔고 비율 기반), 최대 노출 한도
- [x] 슬리피지/수수료를 반영한 신호 필터(과매매 방지) — #52 (`STRATEGY_MIN_EDGE_PCT`, 기본 0.5%)
- [ ] 재시작 시 진입시각/쿨다운 복원(executions 최근 BUY ts 기반) — 현재는 인메모리

## 4단계 — 백테스트 & 성과 (러너·지표 완료: #46/#50)
- [x] 백테스트 러너 — `backtest/run.py` (1분봉 replay; 소스: 업비트 REST 캐시 또는 ClickHouse candles_1m, `--source`). #50에서 데이터원을 raw tick → 1분봉(candles)으로 전환
- [x] 전략별 성과 지표: 누적수익률·승률·MDD·샤프 — `backtest/metrics.py`
- [ ] 대시보드 성과 패널

---

> 참고: 모의 자금 기준이며 수익 최적화가 목표가 아니라 Kafka 파이프라인 위에서 전략을 붙여보는 학습이 목적이다.
