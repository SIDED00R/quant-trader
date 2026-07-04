"""주식 ML 챔피언 일일 스코어러 (단일 책임: 최신 거래일 횡단면 랭킹 산출).

OHLCV+DART 챔피언(LightGBM lambdarank, #158에서 미시구조 증분0 확인 → 제외)을 라벨있는
전 이력으로 학습 → 최신 거래일 횡단면을 예측해 종목 랭킹 반환. build_dataset·_fit_predict 재사용.
라이브 트레이더가 top-N long-only 타깃 구성에 사용한다.
"""
from batch.ml.baseline_lgbm import _fit_predict
from batch.ml.dataset import build_dataset


def _latest_covered(feats):
    """스코어 기준일 = 횡단면 커버리지가 최근 정상치의 50% 이상인 최신 날짜.

    증분 갱신이 일부 종목만 성공한 날(얇은 횡단면)로 top-N을 뽑는 것을 방지 — 그런 날은
    직전 완전한 날짜로 후퇴한다. 기준 = 최근 15개 거래일 횡단면 크기의 최대값.
    """
    sizes = feats.groupby("date").size().sort_index()
    recent = sizes.tail(15)
    ok = recent[recent >= recent.max() * 0.5]
    return ok.index.max()


def score_latest(market: str = "KR", horizon: int = 21, seeds: int = 5,
                 top_n: int = 30, macro: bool = False):
    """(최신봉 날짜, 상위 top_n DataFrame[symbol,date,score]) 반환.

    KR 챔피언 = OHLCV+DART, macro·미시 제외(task3a: macro 포함 시 1.34%→1.17%로 악화).
    """
    feats, cols = build_dataset(market, horizon, fundamentals=True, macro=macro,
                                inst13f=True, sector=True, kr_micro=False)
    latest = _latest_covered(feats)
    train = feats[feats["label"].notna()]
    today = feats[feats["date"] == latest].copy()
    if train.empty or today.empty:
        return latest, today.iloc[0:0][["symbol", "date"]]
    pred, _ = _fit_predict(train, today, cols, seeds, "lambdarank")
    today["score"] = pred
    ranked = today.sort_values("score", ascending=False)[["symbol", "date", "score"]]
    return latest, ranked.head(top_n).reset_index(drop=True)
