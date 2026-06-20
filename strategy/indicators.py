"""기술 지표 순수 함수 (단일 책임: 가격 시퀀스 → 지표값).

전략(rsi/macd/bollinger/breakout)이 **신호 판정에만** 쓴다(금액 계산이 아니므로 float로 충분).
데이터가 부족하면 모두 None을 반환한다. 입력은 Decimal/float 시퀀스 모두 허용한다.
"""


def _floats(prices):
    return [float(p) for p in prices]


def rsi(prices, period=14):
    """단순(SMA식) RSI 0~100. 최소 period+1개 필요, 부족하면 None.

    최근 period개 변화량의 평균이득/평균손실로 RS를 구한다. 손실이 0이면 100(이득有)·50(무변동).
    """
    p = _floats(prices)
    if len(p) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(len(p) - period, len(p)):
        change = p[i] - p[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def bollinger(prices, window=20, k=2.0):
    """(하단, 중심=SMA, 상단=SMA±k·표준편차). 최소 window개 필요, 부족하면 None."""
    p = _floats(prices)
    if len(p) < window:
        return None
    w = p[-window:]
    mid = sum(w) / window
    var = sum((x - mid) ** 2 for x in w) / window
    sd = var ** 0.5
    return mid - k * sd, mid, mid + k * sd


def donchian(prices, lookback=20):
    """직전 lookback개(**현재 봉 제외**)의 (최저, 최고). 최소 lookback+1개 필요, 부족하면 None.

    돌파 판정용: 현재가가 직전 채널 상단 초과면 상향 돌파, 하단 미만이면 하향 이탈.
    """
    p = _floats(prices)
    if len(p) < lookback + 1:
        return None
    prior = p[-lookback - 1:-1]
    return min(prior), max(prior)


class Ema:
    """증분 EMA 업데이터(상태 보유). MACD처럼 경로 의존(이전 EMA에 의존)인 지표용.

    첫 입력을 seed로 쓴다(SMA seed 대신 단순화). update(x)는 갱신된 EMA를 반환한다.
    """
    def __init__(self, span):
        self.k = 2.0 / (span + 1.0)
        self.value = None

    def update(self, x):
        x = float(x)
        self.value = x if self.value is None else x * self.k + self.value * (1.0 - self.k)
        return self.value
