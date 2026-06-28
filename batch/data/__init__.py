"""외부 원본 데이터 수집·영구저장 (단일 책임: API → ClickHouse 원본 테이블).

모델 검증과 무관하게 데이터를 모아 저장(재사용 자산·point-in-time는 나중에 못 되살림).
batch/features는 이 원본에서 피처를 파생한다. batch 전용(프로덕션 이미지 제외).
소스: SEC EDGAR(펀더멘털·13F, 키리스), FRED(매크로, 키), GitHub(US 편입편출), KRX/DART(KR, 키 대기).
"""
