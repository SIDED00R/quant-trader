"""프로듀서 배달 정체 워치독 (단일 책임: 'produce는 되는데 성공 배달이 없음' 감지).

2026-07-18 사고: 단일노드 Kafka 데이터플레인이 행(hang)에 빠졌는데, 프로듀서(ingester)는
delivery 실패(_MSG_TIMED_OUT)를 콜백에서 print만 하고 삼켜 38시간 정지 내내 생존 — 프로세스가 안 죽으니
restart: unless-stopped 자가복구도 발동하지 않았다. 이 워치독은 "마지막 성공 배달 이후의
produce가 쌓인 채 stall_sec가 지났다"를 감지해, 수집기가 스스로 종료(SystemExit)하고
docker 재시작에 복구를 위임하게 한다(업비트·키움 수집기 공용).

오탐 방지: 틱이 없는 한산한 구간은 pending=0이라 절대 발화하지 않는다(마지막 성공 '이후'의
produce만 센다). 기동 직후는 시작 시각을 성공 시각으로 간주해 stall_sec 유예를 준다.
"""
import time

# 정상 배달(<1s) ≪ kafka 재기동·배포(~40s) ≪ 180s < 프로듀서 message.timeout.ms 기본 300s.
DELIVERY_STALL_SEC = 180.0


class DeliveryWatchdog:
    """produce/delivery 기록으로 배달 정체를 판정한다. clock 주입으로 테스트 가능."""

    def __init__(self, stall_sec: float = DELIVERY_STALL_SEC, clock=time.monotonic):
        self._stall_sec = float(stall_sec)
        self._clock = clock
        self._last_ok = clock()          # 기동 유예: 시작 시각을 성공 시각으로 간주
        self.pending = 0                 # 마지막 성공 배달 이후 produce 수

    def record_produce(self) -> None:
        self.pending += 1

    def record_delivery(self, err) -> None:
        """on_delivery 콜백에서 호출. 성공(err is None)만 리셋 — 에러 배달은 생존 증거가 아니다."""
        if err is None:
            self.pending = 0
            self._last_ok = self._clock()

    def stalled(self) -> bool:
        return self.pending > 0 and (self._clock() - self._last_ok) > self._stall_sec
