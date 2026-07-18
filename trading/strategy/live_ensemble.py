"""라이브 추세 신호 워커 (단일 책임: market.ticks → 일봉 마감마다 **부하별** 목표비중 신호 발행).

5단계 다부하 Commander: 검증된 구성의 각 추세속도 부하(5/40·10/60·20/100)가 자기 목표비중을
strategy.signals에 **전략명 태그**로 발행한다(합성은 commander가 가중치로 수행). 기동 시 candles_1d로
워밍업하고 종목별 '현재 UTC 일자'를 추적, 새 일자 진입 시 직전 일자 종가로 각 부하 신호를 1회 발행한다.
**주문은 내지 않는다**(commander 담당). latest offset 구독(룩어헤드 금지). 재시작은 candles_1d 워밍업으로 복원.

LiveEnsemble(순수 상태기)는 Kafka/ClickHouse 비의존이라 단위 테스트 가능. run()이 I/O를 얇게 감싼다.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal

from common.config import ENSEMBLE_SYMBOLS, TOPIC_SIGNALS, TOPIC_TICKS
from common.schemas import Signal
from trading.strategy.ensemble import default_loads
from trading.strategy.trend_signal import TrendSignal

# common.kafka_client(confluent-kafka)는 run()에서만 지연 import — LiveEnsemble 순수 상태기는
# Kafka 비의존이라 단위 테스트 시 무거운 의존성을 끌어오지 않는다.

GROUP_ID = "ensemble-signals"


def utc_day(ts_iso: str):
    """ISO8601(UTC, naive면 UTC로 간주) → date. 일봉 경계 판정용."""
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


class LiveEnsemble:
    """다부하 신호 상태기 — on_tick이 일봉 마감 시 (symbol, bar_day, [(load_name, target)...])를 반환.

    종목별 현재 UTC 일자와 누적 종가를 추적한다. 새 일자 진입 = 직전 일자 종가 확정 → 부하별 신호.
    """
    def __init__(self, symbols, loads=None):
        self.symbols = set(symbols)
        specs = loads or default_loads()                                  # [(name, short, long)]
        self.loads = [(name, TrendSignal(short=s, long=l)) for name, s, l in specs]
        self.cur_day: dict[str, object] = {}
        self.day_close: dict[str, Decimal] = {}

    def _targets(self, symbol, close):
        """각 부하의 (load_name, 목표비중). 부작용: 각 TrendSignal 내부 상태(가격버퍼·래치) 갱신(봉당 1회 호출)."""
        return [(name, sig.update(symbol, close)) for name, sig in self.loads]

    def signals_for(self, symbol) -> list:
        """부하별 마지막 진단(목표비중·단기/장기 SMA·연율변동성·LONG/CASH) — 결정 근거 기록용.

        prime/on_tick으로 각 부하가 최소 1회 갱신된 뒤 호출한다(미갱신 부하는 제외).
        반환 [{"load","target","sma_s","sma_l","ann_vol","state"}] — JSON 직렬화 가능(JSONB 저장용).
        """
        out = []
        for name, sig in self.loads:
            d = sig.last.get(symbol)
            if d is None:
                continue
            out.append({
                "load": name,
                "target": float(d["target"]),
                "sma_s": d["sma_s"],
                "sma_l": d["sma_l"],
                "ann_vol": d["ann_vol"],
                "state": "LONG" if d["long"] else "CASH",
            })
        return out

    def prime(self, history: dict) -> list:
        """history={symbol:[(day, close)...]}(시간오름차순)로 각 부하 워밍업. 마지막 완료봉 부하별 목표를 초기 신호로 반환."""
        out = []
        for sym in self.symbols:
            closes = history.get(sym, [])
            per_load = None
            for _day, close in closes:
                per_load = self._targets(sym, close)     # 매 봉 갱신(부하 deque 워밍업), 마지막 값만 사용
            if per_load is not None and closes:
                self.cur_day[sym] = closes[-1][0]
                self.day_close[sym] = closes[-1][1]
                out.append((sym, closes[-1][0], per_load))
        return out

    def on_tick(self, symbol, price: Decimal, ts_iso: str):
        """틱 1건 처리. 새 UTC 일자 진입 시 직전 일자 종가로 **부하별** 신호 산출·반환, 아니면 None."""
        if symbol not in self.symbols:
            return None
        day = utc_day(ts_iso)
        cur = self.cur_day.get(symbol)
        if cur is None:                 # 첫 관측 — 당일 누적 시작(신호는 다음 마감부터)
            self.cur_day[symbol] = day
            self.day_close[symbol] = price
            return None
        if day <= cur:                  # 같은 날=종가 갱신 / 역행=무시
            if day == cur:
                self.day_close[symbol] = price
            return None
        per_load = self._targets(symbol, self.day_close[symbol])   # 직전 일자(cur) 종가로 부하별 목표
        self.cur_day[symbol] = day
        self.day_close[symbol] = price
        return (symbol, cur, per_load)


def _load_history(symbols) -> dict:
    """candles_1d에서 종목별 (day, close) 시간오름차순 로드(워밍업용). common.marketdata.candles 사용(backtest 비의존)."""
    from common.marketdata.candles import daily_candles
    hist: dict = {s: [] for s in symbols}
    for sym, close, ts in daily_candles(symbols):
        if sym in hist:
            hist[sym].append((datetime.fromtimestamp(ts, timezone.utc).date(), close))
    return hist


def _publish(producer, symbol, load_name, target: Decimal, bar_day) -> None:
    sig = Signal(symbol=symbol, strategy=load_name, target_weight=target,
                 bar_ts=str(bar_day), ts=datetime.now(timezone.utc).isoformat())
    producer.produce(TOPIC_SIGNALS, sig.to_json())
    producer.poll(0)
    print(f"[load] signal {symbol} {load_name} target={target} (bar={bar_day})")


def _publish_all(producer, symbol, per_load, bar_day) -> None:
    for name, t in per_load:
        _publish(producer, symbol, name, t, bar_day)


def run() -> None:
    from common.kafka_client import create_consumer, create_producer   # 지연 import(테스트 시 confluent-kafka 불요)
    state = LiveEnsemble(ENSEMBLE_SYMBOLS)
    hist = _load_history(ENSEMBLE_SYMBOLS)
    producer = create_producer()
    for sym, day, per_load in state.prime(hist):   # 워밍업 후 부하별 현재 목표를 초기 신호로 1회 발행
        _publish_all(producer, sym, per_load, day)
    consumer = create_consumer(GROUP_ID, enable_auto_commit=True, auto_offset_reset="latest")
    consumer.subscribe([TOPIC_TICKS])
    loads = ", ".join(name for name, _ in state.loads)
    print(f"[loads] started — universe={ENSEMBLE_SYMBOLS}, loads=[{loads}], "
          f"warmup={{{', '.join(f'{s}:{len(hist[s])}' for s in ENSEMBLE_SYMBOLS)}}}")
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            data = json.loads(msg.value())
            sym = data.get("symbol")
            if sym not in state.symbols:
                continue
            sig = state.on_tick(sym, Decimal(str(data["price"])), data["trade_ts"])
            if sig:
                _publish_all(producer, sig[0], sig[2], sig[1])
    finally:
        producer.flush()
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[loads] stopped")
