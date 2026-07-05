"""주식 ML 피처 엔지니어링 (단일 책임: OHLCV·외부데이터 → 파생 피처 → 저장).

문헌 조사(docs/ml_features_research.md)에서 추출한 파생 피처를 계산해 ClickHouse
stock_features_daily에 적재한다. 피처 유용성 평가(Rank IC 등)는 batch/ml(evaluate)의 책임.
batch 전용(프로덕션 이미지 제외) — pandas/numpy 의존 허용.
"""
