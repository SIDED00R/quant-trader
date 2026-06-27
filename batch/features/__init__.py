"""주식 ML 피처 엔지니어링 (단일 책임: OHLCV → 파생 피처 → 유용성 IC 테스트 → 저장).

문헌 조사(docs/ml_features_research.md)에서 추출한 OHLCV 파생 피처를 계산하고,
purged-CV IC로 유용성을 측정한 뒤 ClickHouse stock_features_daily에 적재한다.
batch 전용(프로덕션 이미지 제외) — pandas/numpy 의존 허용.
"""
