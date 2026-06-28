"""Purged + Embargo Walk-Forward CV (단일 책임: 날짜 단위 누설없는 분할).

라벨이 미래 horizon일 수익이라, train 끝과 test 시작 사이에 **purge gap(=horizon)+embargo**를 둬
라벨 겹침/자기상관 누설을 차단한다. 분할은 반드시 **날짜 단위**(종목 단위 분할은 같은 날 횡단면 누설).
확장(expanding) train + 연속 test 블록. (López de Prado purged CV의 walk-forward 형태.)
"""
import numpy as np


def purged_walkforward(dates, n_splits: int = 8, horizon: int = 21,
                       embargo: int = 5, min_train: int = 252):
    """정렬된 고유 날짜 배열 → (train_dates, test_dates) 제너레이터.

    train = 처음~ (test_start - horizon - embargo) 이전 모든 날짜(확장).
    test  = 잔여구간을 n_splits로 균등분할한 연속 블록.
    """
    dates = np.array(sorted(set(dates)))
    if len(dates) <= min_train + n_splits:
        raise ValueError(f"날짜 부족: {len(dates)} (min_train={min_train}, n_splits={n_splits})")
    blocks = np.array_split(dates[min_train:], n_splits)
    gap = horizon + embargo
    for blk in blocks:
        if len(blk) == 0:
            continue
        test_start = blk[0]
        cut = np.searchsorted(dates, test_start) - gap     # train 끝 인덱스(누설 차단 purge)
        if cut < min_train // 2:                            # train 너무 짧으면 스킵
            continue
        train = dates[:cut]
        yield train, blk
