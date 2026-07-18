"""수집기 공용 숫자 파싱 (단일 책임: 외부 원본의 결측/비수치 값 → float 강제 변환).

FINRA·SEC·KRX 원본은 빈 문자열·None·비수치 토큰이 섞여 온다 — 실패 시 0.0으로 눌러
적재 스키마(Float64)를 보장한다. finra_short·insider·krx_bulk가 공유(중복 3벌 통합).
"""


def to_float(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0
