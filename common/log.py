"""로깅 표준 설정 (단일 책임: 프로세스 로깅 1회 초기화).

사용: 진입점(데몬 main·api 모듈 최상단·배치 잡 main)에서 log.setup() 1회 호출 →
각 모듈은 `logger = logging.getLogger(__name__)`로 얻어 logger.info/warning/error/exception 사용.
라이브러리 모듈에서 setup()을 호출하지 않는다(진입점만). scripts/·tools/ CLI는 대상 아님
(사용자 대면 print 유지). plain text — 개인 운영 규모, 로그 중앙집계 없음(docker json-file 보관).

config에 의존하지 않는다(config가 로깅을 쓸 수 있고, setup은 무거운 import 전에 돌아야 함) — os.getenv 직접 사용.
"""
import logging
import os
import sys


def setup() -> None:
    """루트 로거 1회 구성(basicConfig는 핸들러가 이미 있으면 no-op이라 멱등). 출력은 stdout(print와 동일 스트림)."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
