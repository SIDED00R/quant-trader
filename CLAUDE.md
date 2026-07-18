# 프로젝트 지침 — quant-trader

이 파일은 **이 저장소에서 작업할 때의 규칙**이다. 전역 지침(`~/.claude/CLAUDE.md`)에 더해 적용한다.
프로젝트 전체 구조·모듈 역할·동작 흐름은 [README.md](README.md), 설계 배경은 [DESIGN.md](DESIGN.md) 참조(여기에 중복 기술하지 않는다).

## 절대 규칙

- **README.md 항상 갱신**: 기능을 추가·변경·이동·삭제할 때마다 같은 PR에서 [README.md](README.md)를 갱신한다.
  대상: ① 폴더 구조 ② "어디에 뭐가 있나" 모듈 역할 맵(§3) ③ 실행 순서·의존(§4) ④ 데이터 소스/브로커(§5) ⑤ 진행 상태.
  *새 모듈/서비스/토픽/브로커/엔드포인트를 추가했는데 README가 그대로면 그 작업은 미완이다.*

## 코드 배치 (단일 책임)

새 코드는 실행 단계에 맞는 폴더에, 파일명이 단일 기능을 드러내게 둔다(전역 지침의 단일 책임 원칙):
- `streaming/` 수집→집계 · `trading/` 신호→체결 · `batch/` 오프라인/백테스트
- `common/` 공용 라이브러리(설정·연결·스키마·HTTP/토큰/레이트리밋 등) · `api/` 서빙
- 기존 파일 확장보다 **새 파일**을 선호. 공통 로직 중복 시 `common/`으로 추출.

## 깨지면 안 되는 경계

- **프로덕션 app 이미지(`Dockerfile`)는 `batch/`를 제외**한다. 따라서 거기에 적재·실행되는 `common/`·`streaming/`·`trading/`·`api/`의 상시 경로는 **`batch.*`를 import 하지 않는다**. 특히 `common/marketdata/candles.py`는 backtest 비의존(프로덕션 일봉 로더). **예외**: `trading/strategy/stock_trade_once.py`·`us_trade_once.py`·`stock_trade_common.py`·`kr_ichimoku_trade_once.py`는 `Dockerfile.batch`(trade 프로파일) 전용 엔트리포인트라 `batch.ml.stock_score`·`batch.backtest.refresh_stock_daily`/`toss_daily`에 의존한다(app 이미지에선 실행되지 않음). 단 새 공용 모듈 `common/marketdata/ichimoku.py`·`common/marketdata/stock_ohlc.py`·`trading/portfolio/paper_ledger.py`는 batch 비의존(app 이미지 안전).
- 폴더/모듈 경로를 바꾸면 import·`docker-compose.yml`(`python -m ...`)·`Dockerfile`(`COPY`)·`infra/*.sh`·docs를 **일괄 갱신**한다.
- **배포 불변식**: compose의 `image:`는 Artifact Registry 참조(CI가 굽고 VM이 pull), startup의 `build_flag()` pull 실패·revision 라벨 불일치 → `--build` 폴백은 **제거 금지**(#94/#99 계승). compose 프로젝트 `name: coin-auto-trader`도 변경 금지(볼륨 prefix — 라이브 데이터 고아화).
- 브로커 분업: **데이터=업비트/토스, 체결=KIS(주식)/시뮬(코인)**. 외부 호출은 `common/rate_limit.py`로 한도 관리.

## 검증

변경 후 최소: `python -m pytest tests/ -q` 통과 + 영향 모듈 import 확인 + (인프라 변경 시) `docker compose config`.
