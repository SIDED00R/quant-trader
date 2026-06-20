"""부하 가중치 정책 (단일 책임: 성과 스코어 → 가드된 가중치 산출, 순수 함수).

5.4 재평가 잡의 핵심 로직. 채택 앙상블이 고정 파라미터라 적응 가중치는 과적합 위험이 크므로
(walk-forward에서 per-fold 최적화 < 고정 입증), 이 정책은 "최적화"가 아니라 **보수적 안전장치**다:
동일가중을 기준선으로 두고, DSR 게이트 미달 부하를 강등(제거 아님)하며, floor/cap·EWMA로 이탈을 억제한다.

- DSR 게이트: OOS 엣지가 통계적으로 유의하지 않은(gate 미만) 부하는 타깃을 0으로 강등 → floor가 살림(demote≠delete).
- floor/cap: 각 가중치를 동일가중×배수 범위로 제한(완전 소멸·독점 차단).
- EWMA: 신규 타깃을 직전 가중치에 천천히 섞음(급변·노이즈 추종 방지).
I/O 없음(DB·백테스트 비의존) → 단위 테스트 가능. 잡(reeval_weights)이 데이터/스코어/저장을 감싼다.
"""


def _clamp_normalize(w: dict, floor: float, cap: float, iters: int = 50) -> dict:
    """합=1을 유지하며 모든 값을 [floor, cap]에 수렴시킨다(반복 클램프-재정규화).

    단발 클램프+재정규화는 재정규화가 경계를 다시 벗어나게 하지만(예: cap 20% 초과),
    반복하면 floor≤1/n≤cap이 성립하는 한 합=1·경계 동시 만족점으로 수렴한다(water-filling).
    """
    out = dict(w)
    for _ in range(iters):
        out = {k: min(cap, max(floor, v)) for k, v in out.items()}
        z = sum(out.values())
        if z <= 0:
            return {k: 1.0 / len(out) for k in out}
        out = {k: v / z for k, v in out.items()}
        if all(floor - 1e-9 <= v <= cap + 1e-9 for v in out.values()):  # 클램프가 더는 안 바뀜 = 수렴
            break
    return out


def compute_weights(scores: dict, gates: dict, prev: dict, *,
                    floor_mult: float, cap_mult: float, dsr_gate: float, ewma_alpha: float) -> dict:
    """부하별 스코어 → 정규화(합=1) 가중치. 가드 적용. 입력 dict들의 키 = 평가 대상 부하 집합(scores 기준).

    scores: {load: 성과 스코어}(클수록 좋음, 음수면 0 취급). gates: {load: DSR/PSR}(<dsr_gate면 강등).
    prev: {load: 직전 가중치}(EWMA용, 없으면 동일가중). floor/cap_mult: 동일가중 대비 하한/상한 배수.
    ewma_alpha: 신규 타깃 반영 비율(0~1). 반환: {load: weight}, 합=1, 각 ∈ [floor, cap].
    """
    loads = list(scores)
    if not loads:
        return {}
    equal = 1.0 / len(loads)
    floor, cap = floor_mult * equal, cap_mult * equal

    # 1) 스코어 → 타깃(게이트 미달은 0으로 강등). 전부 강등이면 편애 없이 동일가중 즉시 반환(EWMA 우회).
    raw = {k: (max(0.0, scores[k]) if gates.get(k, 0.0) >= dsr_gate else 0.0) for k in loads}
    tot = sum(raw.values())
    if tot <= 0:
        return {k: equal for k in loads}
    target = {k: raw[k] / tot for k in loads}

    # 2) EWMA 평활(직전 가중치 대비 느린 갱신; prev 미존재 부하는 동일가중 기준)
    blended = {k: ewma_alpha * target[k] + (1.0 - ewma_alpha) * prev.get(k, equal) for k in loads}

    # 3) floor/cap을 실제 경계로 보장(반복 클램프) — demote≠delete + 독점 차단
    return _clamp_normalize(blended, floor, cap)
