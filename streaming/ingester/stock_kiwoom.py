"""키움 실시간 주식체결 수집기 → stock.ticks (단일 책임: 수집).

연결 직후 LOGIN(접근토큰) → REG(0B 주식체결) 등록, 서버 PING은 받은 그대로 echo해
keepalive를 유지한다. REAL 메시지의 FID values를 Tick으로 정규화해 발행하고,
끊기면 지수 백오프로 재연결한다(코인 ingester/upbit_ws.py 패턴 미러).
출처: docs/kiwoom.md §3 (WebSocket LOGIN/REG/0B/PING).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import websockets

from common.config import (
    KAFKA_BOOTSTRAP_SERVERS,
    KIWOOM_APP_KEY,
    KIWOOM_APP_SECRET,
    KIWOOM_WS_URL,
    STOCK_SYMBOLS,
    TOPIC_STOCK_TICKS,
)
from common.constants import HTTP_MAX_BACKOFF
from common.kafka_client import create_producer
from common.kiwoom_client import get_access_token
from common.schemas import Tick

MAX_BACKOFF = HTTP_MAX_BACKOFF
REAL_TYPE_TRADE = "0B"  # 주식체결
_KST = timezone(timedelta(hours=9))

# 종목별 (체결초, 카운터): 동일 초 내 다중 체결에 단조 seq 부여
# (ClickHouse stock_ticks ORDER BY (symbol, seq) 중복제거 전제 — 키움엔 명시 시퀀스가 없어 합성).
_seq_state: dict[str, tuple[int, int]] = {}
# 초당 카운터 폭. KRX 단일 종목 초당 체결은 이보다 훨씬 작아 충돌 사실상 불가
# (좁히면 초고빈도 구간에서 seq 충돌 → ReplacingMergeTree 묵음 유실 위험). 라이브 실측 후 확정.
_SEQ_PER_SEC = 100_000


def build_login(token: str) -> str:
    return json.dumps({"trnm": "LOGIN", "token": token})


def build_reg(symbols: list[str]) -> str:
    return json.dumps(
        {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [{"item": symbols, "type": [REAL_TYPE_TRADE]}],
        }
    )


def _hhmmss_to_utc(hhmmss: str) -> datetime:
    """체결시간 'HHMMSS'(KST) + 오늘(KST) 날짜 → UTC. 앞 6자리만 사용.

    정규장(09:00~15:30 KST) 당일 처리 가정 — 자정/장외 경계 보정은 라이브 검증 후 도입.
    """
    now_kst = datetime.now(_KST)
    h, m, s = int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6])
    return now_kst.replace(hour=h, minute=m, second=s, microsecond=0).astimezone(
        timezone.utc
    )


def _next_seq(symbol: str, epoch_sec: int) -> int:
    last = _seq_state.get(symbol)
    cnt = last[1] + 1 if last and last[0] == epoch_sec else 0
    _seq_state[symbol] = (epoch_sec, cnt)
    return epoch_sec * _SEQ_PER_SEC + min(cnt, _SEQ_PER_SEC - 1)


def to_tick(item: dict) -> Tick:
    """REAL 0B item → Tick. FID: 10=현재가(전일대비 부호 포함), 15=체결량(+매수/-매도), 20=체결시간.

    ⚠️ FID 의미는 docs/kiwoom.md §3에서 'medium' 신뢰도 — 실제 모의 수신값으로 재확인 후 보정한다.
    """
    v = item["values"]
    qty_raw = v["15"]
    dt = _hhmmss_to_utc(v["20"])
    symbol = item["item"]
    return Tick(
        symbol=symbol,
        price=abs(Decimal(v["10"])),
        volume=abs(Decimal(qty_raw)),
        side="ASK" if qty_raw.startswith("-") else "BID",  # -매도 / +매수
        trade_ts=dt.isoformat(),
        seq=_next_seq(symbol, int(dt.timestamp())),
    )


def _on_delivery(err, msg) -> None:
    if err is not None:
        print(f"[stock-ingester] delivery failed: {err}")


async def run() -> None:
    # 자격증명 미설정 시: 에러 재시도 루프로 churn하지 않고 조용히 idle.
    # (코인 전용 배포에서 stock-ingester가 함께 떠도 무해 — AUTH_ENABLED와 동일한 '키 있으면 활성' 관례.)
    if not (KIWOOM_APP_KEY and KIWOOM_APP_SECRET):
        print("[stock-ingester] KIWOOM_APP_KEY/SECRET 미설정 — 주식 수집 비활성(idle). .env 설정 후 재시작.")
        while True:
            await asyncio.sleep(3600)

    producer = create_producer()
    print(
        f"[stock-ingester] connecting Kiwoom | kafka={KAFKA_BOOTSTRAP_SERVERS} ws={KIWOOM_WS_URL}"
    )
    backoff = 1
    count = 0
    force_token = False  # LOGIN 실패 후 다음 연결에서 토큰 강제 재발급(서버측 무효화 복구)
    try:
        while True:
            try:
                # 키움은 애플리케이션 레벨 PING/PONG을 쓰므로 WS 프로토콜 ping은 끈다.
                async with websockets.connect(KIWOOM_WS_URL, ping_interval=None) as ws:
                    backoff = 1
                    await ws.send(build_login(get_access_token(force=force_token)))
                    force_token = False
                    async for raw in ws:
                        msg = json.loads(raw, parse_float=Decimal)
                        trnm = msg.get("trnm")
                        if trnm == "PING":
                            await ws.send(raw)  # 받은 그대로 echo(keepalive)
                            continue
                        if trnm == "LOGIN":
                            if str(msg.get("return_code")) != "0":
                                force_token = True  # 토큰 무효 가능 → 다음 연결서 강제 재발급
                                raise RuntimeError(f"LOGIN 실패: {msg.get('return_msg')}")
                            await ws.send(build_reg(STOCK_SYMBOLS))
                            print(
                                f"[stock-ingester] LOGIN ok → REG {len(STOCK_SYMBOLS)} symbols"
                            )
                            continue
                        if trnm == "REG":
                            if str(msg.get("return_code")) not in ("0", "None"):
                                print(f"[stock-ingester] REG 경고: {msg.get('return_msg')}")
                            continue
                        if trnm != "REAL":
                            continue
                        for item in msg.get("data", []):
                            if item.get("type") != REAL_TYPE_TRADE:
                                continue
                            try:
                                tick = to_tick(item)
                            except (KeyError, ValueError, TypeError) as e:
                                print(f"[stock-ingester] skip bad real: {e}")
                                continue
                            producer.produce(
                                TOPIC_STOCK_TICKS,
                                key=tick.symbol.encode(),
                                value=tick.to_json(),
                                on_delivery=_on_delivery,
                            )
                            producer.poll(0)
                            count += 1
                            if count % 50 == 0:
                                print(
                                    f"[stock-ingester] produced {count} ticks "
                                    f"(last: {tick.symbol} @ {tick.price})"
                                )
            except (websockets.WebSocketException, OSError, RuntimeError, httpx.HTTPError) as e:
                # httpx.HTTPError: 토큰 발급(get_access_token) HTTP/네트워크 오류도 동일 백오프 재시도.
                print(f"[stock-ingester] connection lost: {e}; reconnect in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
    finally:
        producer.flush(5)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("[stock-ingester] stopped")
