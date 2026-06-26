# 후보 매매 알고리즘 조사·구현 (2단계)

SMA 단일 신호는 추세장에 의존하고 횡보장에서 churn한다(1.5 공정 기준선 −62.32%). 서로 성격이
다른 알고리즘을 도입해 시장 국면별 약점을 분산한다. 채택 여부는 **3단계 일괄 백테스트**로 선별한다.

## 공통 프레임 (`strategy/disciplined.py`)

모든 후보는 `DisciplinedStrategy`를 상속하고 **신호 판정(`_signal`)만** 구현한다. 진입/청산 규율은 공유한다:

- **진입 가드**: 워밍업 → 미보유 → 재진입 쿨다운 → 최대보유 종목 수 → 최소주문액 (1.5단계 봉 단위 값)
- **청산**: 자본보호 `STOP(−1.2%) > TAKE(+2.0%) > TRAIL` (sma_trader 재사용, 매 봉) + **신호 SELL**(전략 고유, `reason='SIGNAL'`, 최소보유·쿨다운 경과 후)
- **사이징**: 고정 비중(`STRATEGY_ORDER_FRACTION_MAX`, 기본 20%) — 신호가 이산이라 SMA의 강도 비례 대신 단순 고정
- 지표 계산은 순수 함수 `strategy/indicators.py`(rsi/bollinger/donchian/Ema)로 분리해 단위 테스트한다.

분류: **평균회귀**(RSI·볼린저) — 과도한 이탈은 되돌아온다는 가정 / **추세추종(모멘텀)**(MACD·돌파) — 움직임은 지속된다는 가정. 두 진영을 함께 두면 한쪽이 약한 국면을 다른 쪽이 보완할 여지가 생긴다(4단계 앙상블의 토대).

---

## 1. RSI (Relative Strength Index) — 평균회귀

- **원리**: 최근 `period`봉의 평균 상승폭/평균 하락폭 비(RS)로 0~100 모멘텀을 환산. 높으면 과매수, 낮으면 과매도.
- **구현**(`strategy/rsi.py`, `indicators.rsi`): period=14, 과매도 ≤30 → BUY, 과매수 ≥70 → SELL. 단순(SMA식) RSI.
- **진입/청산**: RSI 30 이하 매수(반등 기대), 70 이상 신호 청산. STOP/TAKE/TRAIL 병행.
- **강/약점**: 횡보·되돌림 장에 강함. 강한 추세장에선 "과매수에 일찍 팔고 과매도가 더 깊어짐"으로 약함.

## 2. MACD (Moving Average Convergence Divergence) — 추세추종

- **원리**: 단기 EMA(12)−장기 EMA(26) = MACD선, 그 EMA(9) = 시그널선. 두 선의 교차로 추세 전환을 포착.
- **구현**(`strategy/macd.py`, `indicators.Ema`): EMA는 경로 의존이라 종목별 증분 상태로 유지. MACD선이 시그널선을 **상향 교차 → BUY**, **하향 교차 → SELL**. 수렴 전(26봉 미만) 신호 억제.
- **강/약점**: 추세 추종에 강함. 횡보장에서 교차가 잦아 whipsaw(거짓 신호) 다발.

## 3. 볼린저밴드 (Bollinger Bands) — 평균회귀

- **원리**: 중심선 SMA(20)에 ±`k`·표준편차로 변동성 밴드. 가격이 밴드 밖으로 벗어나면 과도한 이탈로 보고 회귀를 기대.
- **구현**(`strategy/bollinger.py`, `indicators.bollinger`): window=20, k=2.0. 하단(SMA−2σ) 이하 → BUY, 상단(SMA+2σ) 이상 → SELL.
- **강/약점**: 변동성 적응형(밴드가 변동성에 따라 확장/수축). 추세 돌파 시 밴드 타고 오르는 구간을 거꾸로 잡아 약함.

## 4. 돌파 / 모멘텀 (Donchian Breakout) — 추세추종

- **원리**: 직전 `lookback`봉의 최고/최저(도닉언 채널). 채널 상단 돌파는 새 상승 추세의 시작으로 본다.
- **구현**(`strategy/breakout.py`, `indicators.donchian`): lookback=20(현재 봉 제외). 직전 최고가 초과 → BUY, 직전 최저가 미만 → SELL.
- **강/약점**: 큰 추세를 초입에 잡음. 박스권에서 가짜 돌파(돌파 후 되돌림)에 약함.

## 5. (참고) 평균회귀 일반

RSI·볼린저가 평균회귀 계열의 대표 구현이다. 추가 변형(z-score 회귀, 페어 트레이딩 등)은 본 단계 범위 밖이며, 필요 시 동일 `DisciplinedStrategy` 패턴으로 확장한다.

---

## 구현 매핑

| 전략 | 파일 | 클래스 | `--strategy` | 분류 |
|---|---|---|---|---|
| RSI | `strategy/rsi.py` | `RSIStrategy` | `rsi` | 평균회귀 |
| MACD | `strategy/macd.py` | `MACDStrategy` | `macd` | 추세추종 |
| 볼린저 | `strategy/bollinger.py` | `BollingerStrategy` | `bollinger` | 평균회귀 |
| 돌파 | `strategy/breakout.py` | `BreakoutStrategy` | `breakout` | 추세추종 |

레지스트리(`strategy/registry.py`)에 등록되어 `python -m batch.backtest.run --strategy <이름>`으로 선택한다.

## 동작 확인 (sanity, KRW-BTC 90일 1분봉)

> ⚠️ 단일 종목·짧은 구간의 **구동 확인용** 수치다. 동일 기간·전 종목·자금에서의 정식 비교·기준치 선별은 **3단계**에서 수행한다.

| 전략 | 누적수익률 | 거래 수 | 승률 |
|---|---|---|---|
| rsi | −14.75% | 888 | 37.4% |
| macd | −17.05% | 1,014 | 32.7% |
| bollinger | −14.85% | 789 | 37.6% |
| breakout | −18.07% | 947 | 28.4% |

모두 구동·체결·청산이 정상 동작한다. 파라미터 튜닝·기간 확장·기준치(Sharpe/승률/MDD) 선별은 3단계 과제.

## 다음 (3단계)
- 전 전략 동일 조건(2년·5종목·자금) 일괄 백테스트 → `docs/baseline.md`의 공정 기준선(−62.32%) 대비 비교
- 기준치(예: Sharpe ≥ X, 승률 ≥ Y, MDD ≤ Z) 정의 → 통과 전략을 부하로 확정 + 초기 가중치 산정
