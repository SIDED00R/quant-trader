"""텔레그램 /chart 봇 (단일 책임: Bot API long-poll → 봉차트 응답). 상시 서비스(수집 VM·app 이미지).

인바운드는 Telegram Bot API getUpdates long-poll(httpx) — 발신용 MTProto 유저세션(notify_telegram)과
분리해 세션 충돌을 피한다. 흐름: `/chart <종목명|티커>`(한글 `/차트` 별칭) → 종목 해석(common.marketdata.stock_names) → 일봉 온디맨드
fetch(common.marketdata.toss_daily — 수집 VM 로컬 CH엔 주식 데이터 없음) → 렌더(common.chart.symbol_chart, KR=주봉+일목
구름 / US=일봉) → Bot API sendPhoto. TELEGRAM_ALLOWED_CHAT_IDS 화이트리스트(비면 전면 거부). 절대 크래시
안 함(예외는 백오프 후 계속). 종목명 인덱스는 repo 번들 사전(common.marketdata.stock_names)에서 로드 —
정확 티커/코드는 사전 무관하게 항상 동작.

실행: python -m api.telegram_bot  (토큰 미설정이면 유휴 — restart 루프 방지)
"""
import io
import sys
import time
import traceback

import httpx

from common import rate_limit
from common.marketdata import stock_names
from common.config import TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_BOT_TOKEN
from common.chart.symbol_chart import KR_FETCH_DAYS, chart_for_symbol
from common.marketdata.toss_daily import fetch_daily

_API = "https://api.telegram.org/bot{token}/{method}"
_KR_FETCH_DAYS, _US_FETCH_DAYS = KR_FETCH_DAYS, 220   # KR은 104주 전 구간 구름에 필요한 깊이(단일 출처)
_HELP = ("📈 사용법: /chart <종목명 또는 티커>  (한글 /차트 도 됨)\n"
         "예) /chart 삼성전자 · /chart 005930 · /chart AAPL\n"
         "국장=주봉+일목 구름 · 미장=일봉")


def parse_command(text: str):
    """텍스트 → ('chart', 질의) | ('help', '') | None. `/chart`·`/차트`(@봇명 허용)·`/help`·`/start`."""
    if not text or not text.strip():
        return None
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("차트", "chart"):
        return ("chart", arg) if arg else ("help", "")
    if cmd in ("help", "start", "도움말"):
        return ("help", "")
    return None


def _url(method: str, token: str) -> str:
    return _API.format(token=token, method=method)


def send_message(token: str, chat_id, text: str) -> None:
    rate_limit.acquire("telegram", "bot")
    try:
        r = httpx.post(_url("sendMessage", token), data={"chat_id": chat_id, "text": text}, timeout=20)
        if r.status_code != 200:                    # best-effort지만 진단 로그는 남긴다
            print(f"[chart-bot] sendMessage {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[chart-bot] sendMessage 실패: {type(e).__name__}: {e}")


def send_photo(token: str, chat_id, png: bytes, caption: str) -> None:
    rate_limit.acquire("telegram", "bot")
    try:
        r = httpx.post(_url("sendPhoto", token), data={"chat_id": chat_id, "caption": caption[:1024]},
                       files={"photo": ("chart.png", io.BytesIO(png), "image/png")}, timeout=30)
        if r.status_code != 200:
            print(f"[chart-bot] sendPhoto {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[chart-bot] sendPhoto 실패: {type(e).__name__}: {e}")


def load_index() -> dict:
    """종목명 검색 인덱스 — repo 번들 사전(common.marketdata.stock_names)에서 로드(이미지 포함, 즉시)."""
    return stock_names.build_index(stock_names.fetch_all())


def handle_chart(query: str, index: dict):
    """질의 처리 → (png, caption) 성공 또는 한글 에러 문자열. 순수-ish(전송은 호출부)."""
    hit = stock_names.resolve(index, query)
    if hit is None:
        return f"❓ 종목을 찾지 못했어요: {query}\n한글명(삼성전자) 또는 티커(005930, AAPL)로 보내주세요"
    market, symbol, name = hit
    days = _KR_FETCH_DAYS if market == "KR" else _US_FETCH_DAYS
    try:
        rows = fetch_daily(symbol, days, log=lambda *a: None)
    except Exception as e:
        return f"⚠️ {symbol} 시세 조회 실패: {type(e).__name__}"
    if not rows:
        return f"⚠️ {symbol} 시세를 가져오지 못했어요(상장폐지·티커 오류일 수 있어요)"
    daily = [(r[1].date(), r[2], r[3], r[4], r[5]) for r in rows]
    try:
        return chart_for_symbol(daily, market, symbol, name if name != symbol else None)
    except ValueError as e:
        return f"⚠️ {symbol} 차트 생성 실패: {e}"


def _handle_update(token: str, upd: dict, index: dict) -> None:
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return
    if chat_id not in TELEGRAM_ALLOWED_CHAT_IDS:    # 빈 화이트리스트 = 전면 거부(fail-closed — 문서 기본값)
        print(f"[chart-bot] 비허용 chat_id={chat_id} drop")
        return
    parsed = parse_command(msg.get("text", ""))
    if parsed is None:
        return
    if parsed[0] == "help":
        send_message(token, chat_id, _HELP)
        return
    res = handle_chart(parsed[1], index)
    if isinstance(res, tuple):
        send_photo(token, chat_id, res[0], res[1])
    else:
        send_message(token, chat_id, res)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    token = TELEGRAM_BOT_TOKEN
    if not token:
        print("[chart-bot] TELEGRAM_BOT_TOKEN 미설정 — 유휴(재시작 루프 방지). 토큰 주입 후 컨테이너 재시작 필요.")
        while True:                       # exit 0 시 restart:unless-stopped 루프 방지 → 유휴 대기
            time.sleep(3600)
    index = load_index()
    offset, backoff = 0, 1
    print(f"[chart-bot] 시작 — 허용 chat {len(TELEGRAM_ALLOWED_CHAT_IDS)}개, 종목명 {len(index.get('rows', []))}개")
    with httpx.Client(timeout=70) as client:
        while True:
            try:
                r = client.get(_url("getUpdates", token), params={"offset": offset, "timeout": 50})
                r.raise_for_status()
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        _handle_update(token, upd, index)
                    except Exception:
                        traceback.print_exc()
                backoff = 1
            except Exception as e:                           # 409(다중 소비자)·네트워크 등 → 백오프 후 계속(크래시 금지)
                print(f"[chart-bot] poll 오류(재시도): {type(e).__name__}: {e}")
                time.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    raise SystemExit(main())
