"""라이브 앙상블 신호 워커 (단일 책임: market.ticks → 일봉 마감마다 앙상블 목표비중 신호 발행).

검증된 앙상블(일봉 3속도, band 0.5)을 라이브로 구동한다. 기동 시 candles_1d로 워밍업하고,
market.ticks를 소비하며 종목별 '현재 UTC 일자'를 추적한다. 새 일자로 넘어가면 직전 일자 종가가
확정되므로 그 종가로 앙상블 목표비중을 산출해 strategy.signals로 발행한다(일봉당 1회).
**주문은 내지 않는다** — 주문은 commander가 별도 담당(consequential 단계 분리). latest offset 구독이라
기동 시 과거 틱을 재생하지 않는다(룩어헤드 금지). 인메모리 상태는 재시작 시 candles_1d 워밍업으로 복원.

LiveEnsemble(순수 상태기)는 Kafka/ClickHouse 비의존이라 단위 테스트 가능. run()이 I/O를 얇게 감싼다.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal

from common.config import ENSEMBLE_SYMBOLS, TOPIC_SIGNALS, TOPIC_TICKS
from common.kafka_client import create_consumer, create_producer
from common.schemas import Signal
from strategy.ensemble import EnsembleStrategy

GROUP_ID = "ensemble-signals"


def utc_day(ts_iso: str):
    """ISO8601(UTC, naive면 UTC로 간주) → date. 일봉 경계 판정용."""
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


class LiveEnsemble:
    """앙상블 신호 상태기 — on_tick이 일봉 마감 시 (symbol, bar_day, target)을 반환(없으면 None).

    종목별 현재 UTC 일자와 누적 종가를 추적한다. 새 일자 진입 = 직전 일자 종가 확정 → 신호 1건.
    """
    def __init__(self, symbols, strategy=None):
        self.symbols = set(symbols)
        self.ensemble = strategy or EnsembleStrategy()
        self.cur_day: dict[str, object] = {}
        self.day_close: dict[str, Decimal] = {}
        self.target: dict[str, Decimal] = {}

    def prime(self, history: dict) -> list:
        """history={symbol:[(day, close)...]}(시간오름차순)로 워밍업. 마지막 완료봉 목표비중을 초기 신호로 반환."""
        out = []
        for sym in self.symbols:
            closes = history.get(sym, [])
            t = None
            for _day, close in closes:
                t = self.ensemble.combined_target(sym, close)
            if t is not None and closes:
                self.cur_day[sym] = closes[-1][0]
                self.day_close[sym] = closes[-1][1]
                self.target[sym] = t
                out.append((sym, closes[-1][0], t))
        return out

    def on_tick(self, symbol, price: Decimal, ts_iso: str):
        """틱 1건 처리. 새 UTC 일자 진입 시 직전 일자 종가로 신호 산출·반환, 아니면 None."""
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
        prev_close = self.day_close[symbol]   # 직전 일자(cur) 종가 확정
        t = self.ensemble.combined_target(symbol, prev_close)
        self.target[symbol] = t
        self.cur_day[symbol] = day
        self.day_close[symbol] = price
        return (symbol, cur, t)


def _load_history(symbols) -> dict:
    """candles_1d에서 종목별 (day, close) 시간오름차순 로드(워밍업용). common.candles 사용(backtest 비의존)."""
    from common.candles import daily_candles
    hist: dict = {s: [] for s in symbols}
    for sym, close, ts in daily_candles(symbols):
        if sym in hist:
            hist[sym].append((datetime.fromtimestamp(ts, timezone.utc).date(), close))
    return hist


def _publish(producer, symbol, target: Decimal, bar_day) -> None:
    sig = Signal(symbol=symbol, strategy="ensemble", target_weight=target,
                 bar_ts=str(bar_day), ts=datetime.now(timezone.utc).isoformat())
    producer.produce(TOPIC_SIGNALS, sig.to_json())
    producer.poll(0)
    print(f"[ensemble] signal {symbol} target={target} (bar={bar_day})")


def run() -> None:
    state = LiveEnsemble(ENSEMBLE_SYMBOLS)
    hist = _load_history(ENSEMBLE_SYMBOLS)
    producer = create_producer()
    for sym, day, t in state.prime(hist):   # 워밍업 후 현재 목표비중을 초기 신호로 1회 발행
        _publish(producer, sym, t, day)
    consumer = create_consumer(GROUP_ID, enable_auto_commit=True, auto_offset_reset="latest")
    consumer.subscribe([TOPIC_TICKS])
    print(f"[ensemble] started — universe={ENSEMBLE_SYMBOLS}, "
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
                _publish(producer, sig[0], sig[2], sig[1])
    finally:
        producer.flush()
        consumer.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[ensemble] stopped")
