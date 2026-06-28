"""주식 ML 모델링 (단일 책임: 피처 → 라벨·CV·학습·평가 → 횡단면 수익예측).

batch/features(피처 엔지니어링)와 분리. 모델 무관 공유 인프라:
- dataset: 피처+라벨 조립(시장별, KR은 누설없는 US 컨텍스트 포함)
- cv: purged/embargo walk-forward(날짜 단위, 라벨 누설 차단)
- evaluate: Rank IC/ICIR/NW-t + 롱숏 Sharpe
- baseline_lgbm: LightGBM 베이스라인(시드앙상블) — 모든 모델의 must-beat 게이트
batch 전용(프로덕션 이미지 제외).
"""
