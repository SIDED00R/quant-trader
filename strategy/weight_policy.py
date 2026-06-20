"""부하 가중치 정책 (단일 책임: 성과 스코어 → 가드된 가중치 산출, 순수 함수).

5.4 재평가 잡의 핵심 로직. 채택 앙상블이 고정 파라미터라 적응 가중치는 과적합 위험이 크므로
(walk-forward에서 per-fold 최적화 < 고정 입증), 이 정책은 "최적화"가 아니라 **보수적 안전장치**다:
동일가중을 기준선으로 두고, DSR 게이트 미달 부하를 강등(제거 아님)하며, floor/cap·EWMA로 이탈을 억제한다.

- DSR 게이트: OOS 엣지가 통계적으로 유의하지 않은(gate 미만) 부하는 타깃을 0으로 강등 → floor가 살림(demote≠delete).
- floor/cap: 각 가중치를 동일가중×배수 범위로 제한(완전 소멸·독점 차단).
- EWMA: 신규 타깃을 직전 가중치에 천천히 섞음(급변·노이즈 추종 방지).
I/O 없음(DB·백테스트 비의존) → 단위 테스트 가능. 잡(reeval_weights)이 데이터/스코어/저장을 감싼다.
"""


def compute_weights(scores: dict, gates: dict, prev: dict, *,
                    floor_mult: float, cap_mult: float, dsr_gate: float, ewma_alpha: float) -> dict:
    """부하별 스코어 → 정규화(합=1) 가중치. 가드 적용. 입력 dict들의 키 = 평가 대상 부하 집합(scores 기준).

    scores: {load: 성과 스코어}(클수록 좋음, 음수면 0 취급). gates: {load: DSR/PSR}(<dsr_gate면 강등).
    prev: {load: 직전 가중치}(EWMA용, 없으면 동일가중). floor/cap_mult: 동일가중 대비 하한/상한 배수.
    ewma_alpha: 신규 타깃 반영 비율(0~1). 반환: {load: weight}, 합=1.
    """
    loads = list(scores)
    if not loads:
        return {}
    equal = 1.0 / len(loads)
    floor, cap = floor_mult * equal, cap_mult * equal

    # 1) 스코어 → 타깃(게이트 미달은 0으로 강등; 이후 floor가 최소치를 보장 = demote≠delete)
    raw = {k: (max(0.0, scores[k]) if gates.get(k, 0.0) >= dsr_gate else 0.0) for k in loads}
    tot = sum(raw.values())
    target = {k: (raw[k] / tot if tot > 0 else equal) for k in loads}   # 전부 강등 → 동일가중 폴백

    # 2) EWMA 평활(직전 가중치 대비 느린 갱신; prev 미존재 부하는 동일가중 기준)
    blended = {k: ewma_alpha * target[k] + (1.0 - ewma_alpha) * prev.get(k, equal) for k in loads}

    # 3) floor/cap 클램프 후 재정규화(합=1). 클램프-재정규화는 근사이나 floor/cap이 보수적이라 이탈 미미.
    clamped = {k: min(cap, max(floor, blended[k])) for k in loads}
    z = sum(clamped.values())
    return {k: clamped[k] / z for k in loads} if z > 0 else {k: equal for k in loads}
